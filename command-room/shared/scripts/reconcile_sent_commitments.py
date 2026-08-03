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
    "subject": str|None, "body": str|None,
    "recipient_names": [str, ...],          # Bug #103 recall fallback
    "has_attachment": bool,                 # SENTMATCH signal A
    "thread_id": str|None}, ...]            # SENTMATCH signal B
Every field past `body` is optional and absent → the behavior that field
enables simply does not fire (never a guess).
"""
from __future__ import annotations

import datetime as _dt
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
    DELIVERY_BASIS as _DELIVERY_BASIS,
    THREAD_BASIS as _THREAD_BASIS,
    AMBIGUOUS_DELIVERY_BASIS as _AMBIGUOUS_DELIVERY_BASIS,
)
from connector_adapters.provenance import (  # noqa: E402
    canonical_dedup_key,
    is_same_artifact,
    primary_artifact_key,
    resolve_mail_provider,
)


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


class PrimaryUserUnresolvedError(RuntimeError):
    """Bug #102 — `resolve_primary_user` came back None/empty.

    The owner gate on every match path is `data.owner_id == user_person_id`.
    With no user it matches nothing, the run closes zero, and the audit event
    lands CLEAN with `n_closed: 0` — byte-identical to a healthy run that
    genuinely had nothing to close. `validate_reconcile_ran` then returns
    ok=True. That is the dead-rail shape the whole receipt contract exists to
    make impossible: a fence present, tested, and inert, reporting success.

    (The resolver returns `person_001` at a workspace root and None at `_hq`,
    so the difference between working and silently dead is which path a caller
    passed — exactly the failure that must never be quiet.)

    So the orchestrator ABORTS instead: no audit event, no cursor advance, a
    loud receipt on stderr, and this exception for the caller to surface.
    """


def _empty_signal_fields() -> dict:
    """The SENTMATCH observability block (review F-4), zeroed.

    `n_fetched` is the denominator; the `*_field_present` counts say whether
    the FETCH carried the field at all, and the `n_with_*` counts say how many
    messages actually had one. `n_closed_on_*` closes the loop from field to
    outcome. All seven read together answer the one question a healthy-looking
    zero cannot: did the delivery checks RUN, or was there nothing to find?

    EVORDER adds `n_stale_evidence_skipped` — candidates dropped because the
    message predates the commitment it would have closed. A fence that drops
    silently is how F-11 stayed invisible for a week, so layer 3 counts.
    """
    return {
        "n_fetched": 0,
        "n_attachment_field_present": 0,
        "n_with_attachment": 0,
        "n_thread_field_present": 0,
        "n_with_thread_ref": 0,
        "n_closed_on_delivery": 0,
        "n_closed_on_thread": 0,
        "n_stale_evidence_skipped": 0,
    }


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
    provider=None,
    exclude_captured_since=None,
    workspace_root=None,
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
        "signal_fields": { ... },                       # SENTMATCH F-4: did the fetch carry the fields?
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

    RECONFENCE (v5.4.x — the AUTOAPPLY §6 fence, mirrored onto this path):
    each message is now scored with `send_source_ref` set to its own
    canonical key, and `exclude_captured_since` forwarded from the caller.
    Layer 1 subsumes and widens the BUG-3719 filter above (that filter is a
    single-key identity check and cannot see a C4 merge survivor's
    `merged_source_refs`); layer 2 covers same-fire siblings, which have no
    ref relationship to the send at all. `exclude_captured_since=None` (the
    default) leaves this function byte-identical to pre-RECONFENCE.

    SENTMATCH — two non-title closure bases ride the same call, both
    fed from fields on the message dict and both inert when absent:
    `has_attachment` (signal A, delivery evidence) and `thread_id` (signal B,
    the thread prior — canonicalized here against `provider`, the same way the
    RECONFENCE layer-1 key already is). A message shape without those keys
    reproduces pre-SENTMATCH behavior byte-for-byte.

    `signal_fields` (review F-4) — the counters that make that inertness
    VISIBLE. Both fields are a prose-level contract in reconcile-sent's Step 2,
    so a fetch that silently stops carrying them turns both bases off while the
    run still writes a healthy audit: zero delivery-closes then reads exactly
    like "nothing was deliverable". That is the same silent zero the Bug #102
    abort just closed on the adjacent rail, one level up. PRESENCE is counted
    separately from TRUTH — a message that genuinely has no attachment is a
    fact about the mail; a message whose dict never carried the key is a fact
    about the FETCH, and only the second one means the rail is dead.

    `workspace_root` (F-28 post-review F-1) — forwarded to Path 1 so the roster
    reader can resolve a free-text `counterparty_name` against the entity graph
    and see that one person written as BOTH an id and that person's name is ONE
    counterparty. Without it Path 1 carries a parameter nothing fills, which is
    the AUTOAPPLY F-5 dead-rail shape: the review found this rail scoring the
    F-28 defect shape as `partial` while the id-only control auto-closed. The
    function stays PURE in the sense that matters — it does no I/O of its own;
    it hands the path down to the one reader that reads the entity graph, and
    only when a caller supplies it. `None` (the default) is byte-identically
    pre-F-28, so every existing caller and test is unaffected.

    Pure: no I/O, no clock. The caller emits the events + persists the cursor.
    """
    if not user_person_id:
        # Bug #102 — the pure matcher keeps its documented safe degrade (an
        # empty result, no raise) because other callers depend on it, but the
        # silent part is what made the bug invisible for a release. The
        # orchestrator below turns this same condition into a hard abort; here
        # it is at least audible.
        print(
            "reconcile_sent: no user_person_id — the owner gate matches "
            "nothing and this run can only return zero closures. Resolve the "
            "user with primary_user.resolve_primary_user (Bug #102).",
            file=sys.stderr,
        )
        return {"auto_close": [], "pending": [], "partial": [],
                "cursor_ts": None, "signal_fields": _empty_signal_fields()}

    best: dict[str, dict] = {}      # commitment_id → best proposal so far
    partial_by_cid: dict[str, dict] = {}  # commitment_id → accumulated receipts
    cursor_ts = None
    signals = _empty_signal_fields()
    # EVORDER — one dict for the whole run; the matcher increments it per
    # dropped candidate and we fold the total into the receipt below. Kept
    # outside the message loop so the count is the run's, not the last
    # message's.
    _evorder_diag: dict = {}

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
        #
        # MAILSEAM item 4: identity is compared through `is_same_artifact`,
        # never a single key built from a literal. `provider or "gmail"` here
        # built `gmail:<id>` against commitments stored as `superhuman:<id>`,
        # so the guard excluded nothing and a wide catch-up closed the very
        # promise that message had opened. The predicate matches both labels
        # when the provider is known (a workspace has rows from before and
        # after its backend was declared) and matches on the native id when it
        # is not — this function is pure, so an unresolved provider is normal,
        # not exceptional.
        mid = str(msg.get("message_id") or "").strip()
        own_key = primary_artifact_key(provider, mid)
        # SENTMATCH signal B — the CONVERSATION key, derived exactly like the
        # message key above so one thread never reads as two. Both lines moved
        # off the gmail literal together, as SENTMATCH's build record required.
        tid = str(msg.get("thread_id") or "").strip()
        own_thread_key = primary_artifact_key(provider, tid)
        # Review F-4 — count the FETCH, not just the outcome. `in msg` is the
        # presence test on purpose: `has_attachment: False` is the connector
        # answering, an absent key is the connector never being asked.
        signals["n_fetched"] += 1
        if "has_attachment" in msg:
            signals["n_attachment_field_present"] += 1
        if msg.get("has_attachment"):
            signals["n_with_attachment"] += 1
        if "thread_id" in msg:
            signals["n_thread_field_present"] += 1
        if own_thread_key:
            signals["n_with_thread_ref"] += 1
        opens_for_msg = open_commitments
        if mid:
            opens_for_msg = [
                c for c in open_commitments
                if not is_same_artifact(canonical_dedup_key(event=c),
                                        provider, mid)
            ]

        results = match_send_to_commitments(
            open_commitments=opens_for_msg,
            sender_person_id=user_person_id,
            recipient_person_ids=msg.get("recipient_person_ids") or [],
            subject=msg.get("subject"),
            body=msg.get("body"),
            # RECONFENCE layer 1 — the ref of the message being scored. The
            # BUG-3719 filter above is a single-key identity check; this is
            # the full attribution test (merged_source_refs, alternate
            # spellings, the structured-provenance and gmail-id channels),
            # and it lives in cru_match so Path 1's other callers inherit it.
            send_source_ref=own_key,
            # RECONFENCE layer 2 — commitments this same fire captured are
            # not independent evidence for closing themselves. None (the
            # default) = pre-RECONFENCE behavior, byte-identical.
            exclude_captured_since=exclude_captured_since,
            # Bug #103 recall fallback: recipient display names + email local-parts
            # so a commitment that names the recipient in its title ("Send Bo a
            # recap") still matches even when the counterparty isn't linked into
            # person_ids or has no email on file.
            recipient_names=msg.get("recipient_names") or [],
            # SENTMATCH signal A — the connector's attachment flag for THIS
            # message. Coerced with bool() so a provider that reports an
            # attachment COUNT or a list still reads correctly, and an absent
            # key stays False: no evidence is not weak evidence.
            has_attachment=bool(msg.get("has_attachment")),
            # SENTMATCH signal B — inert when the fetch carried no thread id.
            send_thread_ref=own_thread_key,
            # EVORDER layer 3 — when this message was actually sent. A send
            # cannot be evidence for a promise captured after it. Absent from
            # the fetch → the guard is inert, never a guess.
            send_ts=msg.get("ts"),
            diagnostics=_evorder_diag,
            # F-28 — the entity graph is what tells the roster reader that one
            # person written as an id AND that person's name is one
            # counterparty, not two. Absent → the raw union, pre-F-28.
            workspace_root=workspace_root,
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
            # SENTMATCH — the evidence line names the BASIS, because the
            # change feed is where the user decides whether to `undo`. "matched
            # your sent message" is true of a title echo and misleading of a
            # delivery: they should not read the same.
            basis = r.get("close_basis") or ""
            if basis == _DELIVERY_BASIS:
                lede = "you sent the attachment"
            elif basis == _AMBIGUOUS_DELIVERY_BASIS:
                lede = "you sent an attachment that fits more than one open item"
            elif basis == _THREAD_BASIS:
                lede = "matched your reply on this thread"
            else:
                lede = "matched your sent message"
            proposal = {
                "commitment_id": cid,
                "score": r.get("score"),
                "title": r.get("title") or "",
                "owner_id": r.get("owner_id") or user_person_id,
                "primary_thread_id": r.get("primary_thread_id") or "",
                "message_id": msg.get("message_id") or "",
                "ts": ts or "",
                "recommendation": rec,
                "close_basis": basis,
                "evidence": (
                    lede
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

    # Review F-4 — close the loop from field to outcome. Counted AFTER the
    # FS-11 promotion so these are the closures that actually happened, not
    # the matcher's intermediate grades.
    for p in auto_close:
        if p.get("close_basis") == _DELIVERY_BASIS:
            signals["n_closed_on_delivery"] += 1
        elif p.get("close_basis") == _THREAD_BASIS:
            signals["n_closed_on_thread"] += 1

    # EVORDER — fold layer 3's drop count into the receipt. A non-zero value is
    # the fence working, not an error: it says "N candidates were older than
    # their own evidence and were refused."
    signals["n_stale_evidence_skipped"] = int(
        _evorder_diag.get("stale_evidence_dropped", 0))

    return {"auto_close": auto_close, "pending": pending, "partial": partial,
            "cursor_ts": cursor_ts, "signal_fields": signals}


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


def _record_blocked_run(workspace_root, events_path, cursor_before, *,
                        reason, source_skill, fired_via, provider=None) -> dict:
    """MAILSEAM item 8 — the receipt for a fire whose Sent READ could not run.

    Writes a `sent_reconcile` audit event stamped `status: "blocked"` with the
    reason, leaves the cursor exactly where it was, and returns a receipt in
    the same shape as a real run so callers need no new branch. The audit is
    still written — a blocked fire that leaves NO trace is indistinguishable
    from a fire that never happened, and the whole point of this event is that
    a validator can tell those apart. `validate_reconcile_ran` reads the status
    and refuses it, so a blocked run can never be reported as a clean zero."""
    from next_seq import next_seq as _next_seq
    from atomic_write import atomic_append_jsonl as _append
    from cru_match import _now_iso as _audit_ts

    audit_event = {
        "seq": _next_seq(str(events_path)),
        "ts": _audit_ts(),
        "type": "sent_reconcile",
        "source_skill": source_skill,
        "data": {
            "task_id": "reconcile-sent",
            "kind": "reconcile-sent",
            "status": "blocked",
            "blocked_reason": reason,
            "fired_via": fired_via,
            "cursor_from": cursor_before,
            "cursor_to": cursor_before,   # never advanced over an unread window
            "sent_scanned_count": 0,
            "n_closed": 0,
            "n_pending": 0,
            "n_partial_receipts": 0,
            "mail_provider": provider,
            "signal_fields": _empty_signal_fields(),
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

    summary = (f"Sent-mail reconciliation did not run: {reason}. Nothing was "
               "read, so nothing was closed or opened, and the cursor stayed "
               "where it was — the next run picks up the whole window.")
    print("reconcile-sent BLOCKED: " + reason, file=sys.stderr)
    return {
        "ran": False,
        "blocked": True,
        "blocked_reason": reason,
        "cursor_before": cursor_before,
        "cursor_after": cursor_before,
        "cursor_advanced": False,
        "n_fetched": 0,
        "n_open_before": 0,
        "n_auto_closed": 0,
        "n_pending": 0,
        "events_written": 0,
        "reviews_written": 0,
        "resolved": [],
        "pending": [],
        "n_partial_receipts": 0,
        "partial": [],
        "partial_propose_closure": [],
        "partial_skipped_names": [],
        "signal_fields": _empty_signal_fields(),
        "n_opened": 0,
        "opened": [],
        "capture": None,
        "mail_provider": provider,
        "summary": summary,
    }


def reconcile_and_receipt(
    workspace_root,
    sent_messages,
    *,
    user_person_id,
    source_skill="morning-briefing",
    outcome_watch_summary=None,
    fired_via="scheduled",
    sent_commitment_items=None,
    provider=None,
    exclude_captured_since=None,
    fetch_blocked=None,
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
        "signal_fields": dict,       # SENTMATCH F-4 — did the delivery checks run?
        "summary": str,              # code-generated line the brief pastes verbatim
      }

    `provider` (MAILSEAM item 4/5) — the provider tag of the mail connector
    the batch was read from (`DiscoveryResult.platform` / the declared email
    backend). None is not "Gmail": it means the caller resolved nothing, and
    this function then reads the workspace's DECLARED email backend rather
    than falling to a literal. The old `provider="gmail"` default silently
    mislabelled every ref written on a non-Gmail backend AND built a dedup key
    that matched no commitment on disk — which turned the BUG-3719 self-closure
    guard off without failing anything.

    `fetch_blocked` (MAILSEAM item 8) — a plain-English reason the Sent READ
    could not happen (no mail connector, connector budget exhausted, an
    unclassified account). Passing it records the run as BLOCKED: the audit
    event carries `status: "blocked"` + the reason, `validate_reconcile_ran`
    refuses it, and the summary says what was missing. Without it, a run that
    never read anything wrote the identical clean `sent_scanned_count: 0`
    audit as a run that read everything and found nothing — the dead-rail
    shape this receipt contract exists to make impossible. A blocked run
    closes nothing, opens nothing, and NEVER advances the cursor.

    `signal_fields` also rides the `sent_reconcile` audit event, and when a
    fetch carried NEITHER `has_attachment` nor `thread_id` on any message the
    `summary` says so in plain language. That combination is the difference
    between "the delivery checks found nothing" and "the delivery checks never
    ran" — two states that otherwise produce the identical healthy zero.

    `exclude_captured_since` (RECONFENCE) — the ISO timestamp the caller
    recorded at the START of this fire, before any phase wrote. Commitments
    captured at or after it are excluded from send scoring: one orchestrator
    fire that captures in an early phase and reconciles sent mail in a later
    one would otherwise score a commitment against the very fire that wrote
    it (the dogfood's 14:38 capture questioned at 14:40). Anything predating
    the fire stays fully matchable, so a send that genuinely fulfills an
    earlier promise still closes it. None (the default) = pre-RECONFENCE
    behavior, byte-identical. Layer 1 needs no wiring here — `reconcile_sent`
    derives each message's own ref internally.

    Idempotent across a day: a re-run fetches only newer Sent mail; an empty
    batch closes nothing and leaves the cursor where it is; a re-extracted
    commissive is skipped by (source_ref, title) + restatement dedup.

    RAISES `PrimaryUserUnresolvedError` when `user_person_id` is falsy (Bug
    #102). Nothing is read, nothing is written, and no `sent_reconcile` audit
    event exists for the run — so `validate_reconcile_ran` reports the run as
    not-having-happened, which is the truth. Before this, the same state wrote
    a clean `n_closed: 0` audit and advanced the cursor past mail it had never
    really matched.
    """
    if not user_person_id:
        msg = (
            "reconcile-sent ABORTED: the primary user is unresolved "
            "(resolve_primary_user returned None/empty). Every owner gate "
            "would match nothing and this run would write a clean audit "
            "claiming zero to close. No audit event written, cursor NOT "
            "advanced. Fix: pass the WORKSPACE ROOT (not _hq) to "
            "resolve_primary_user, or set workspace.user_person_id in "
            "entities.json (Bug #102)."
        )
        print(msg, file=sys.stderr)
        raise PrimaryUserUnresolvedError(msg)

    # MAILSEAM: resolve the provider ONCE, here, and use it for every ref this
    # run compares or writes. An explicit argument wins; otherwise the declared
    # email backend answers. Unresolved stays unresolved — the identity helpers
    # degrade honestly, and the receipt says so below.
    provider = resolve_mail_provider(workspace_root, provider)

    cursor_before, raw = _read_cursor(workspace_root)
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"

    # MAILSEAM item 8 — a read that never happened is not a read that found
    # nothing. Record the run as blocked and stop: no matching (there is
    # nothing to match), no capture, and above all no cursor advance, which
    # would otherwise skip the window forward past mail this run never saw.
    blocked = str(fetch_blocked or "").strip()
    if blocked:
        return _record_blocked_run(
            workspace_root, events_path, cursor_before,
            reason=blocked, source_skill=source_skill, fired_via=fired_via,
            provider=provider,
        )

    # F-28 — the workspace goes to the projector too, so the MC1 all-received
    # stamp on the rows this driver matches agrees with the roster reader the
    # matcher below uses. One workspace, one answer, per fire.
    opens = load_open_commitments(str(events_path), workspace_root=workspace_root)
    n_open_before = len(opens)

    res = reconcile_sent(opens, sent_messages or [], user_person_id=user_person_id,
                         provider=provider,
                         exclude_captured_since=exclude_captured_since,
                         # F-28 — this wrapper HOLDS the workspace and used to
                         # keep it, leaving Path 1's roster fix unreachable on
                         # the rail that fires daily.
                         workspace_root=workspace_root)
    auto_close = res["auto_close"]
    pending = res["pending"]
    partial = res.get("partial") or []
    signal_fields = res.get("signal_fields") or _empty_signal_fields()

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
    #
    # WATCHGATE N-2 (RIDERS1 item 5): this used to hand-build the event dict.
    # `cru_match.build_pending_review_event` is THE writer for this type, and it
    # is the only thing that persists `evidence_ts` — WHEN the evidence was
    # observed — which the shared bulk-accept fence needs for its apply-moment
    # ordering check. A hand-built row could not carry it, so every proposal
    # this rail wrote screened as STRONG by construction: not because the match
    # was strong, but because the fields that could weaken it were absent.
    # `title` (FB-19) and the two rail-specific keys ride the same event; the
    # writer owns the shape, the caller owns its own extras.
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
                evidence=p.get("evidence") or "matched an outbound send",
                # None: the gate auto-stamps seq inside the writer lock.
                next_seq=None,
                # FB-19: the row's own name. Without it the LB1 adapter has no
                # title, `_row_name` falls back to the shape label, and the card
                # renders a bare shape name with nothing to identify WHAT
                # matched (the live 2026-07-16 render).
                title=p.get("title") or "",
                # The send's own time. A reply/send cannot be evidence for a
                # promise captured after it, and the fence checks exactly that
                # at apply time — but only if the timestamp was persisted.
                evidence_ts=p.get("ts") or None,
                # NOT ASSESSED, deliberately. `has_completion_signal` is the
                # matcher's own fulfillment finding, and the SENT matcher
                # computes none — its analogue is the close_basis, and deriving
                # a boolean from that here would re-grade every title-band
                # confirm on this rail. `None` means "the caller could not
                # judge" and weakens nothing, which is the honest report.
                has_completion_signal=None,
            )
            ev["data"].update({
                # FS-11: only genuinely ambiguous matches reach here now
                # (unambiguous moderate matches auto-closed above). Carry a TTL
                # so an un-adjudicated proposal expires instead of accumulating;
                # the LB1 review adapter drops expired ones.
                "ttl_days": REVIEW_PROPOSAL_TTL_DAYS,
                "ambiguous": True,
            })
            append_event(events_path, [ev], holder=source_skill)
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
            # MAILSEAM — the provider every ref this run wrote or compared was
            # attributed to. None means the workspace declares no email
            # backend; the refs then carry the legacy anchor and the audit
            # says so rather than the log implying Gmail was verified.
            "mail_provider": provider,
            # SENTMATCH review F-4 — did the delivery checks RUN? Folded into
            # the SAME audit event as the outcome watch (the B6 precedent):
            # one fire, one verifiable trace, free-form `data`, no schema
            # change. Without this, "the fix didn't fire" and "nothing was
            # deliverable" are the same healthy-looking zero.
            "signal_fields": signal_fields,
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
    # SENTMATCH review F-4 — the counters land in the audit for a validator;
    # this sentence is for the HUMAN reading the receipt, and it goes LAST
    # because it is a caveat on everything above it. It fires only when the
    # fetch carried NEITHER field on ANY message: the dead-rail state, where
    # the delivery checks did not run at all and the zero above therefore
    # reads as "nothing was deliverable". Plain language, no field names
    # (Rule 4). A fetch that carried the fields and simply found no
    # attachments says nothing extra — that is a normal, honest zero.
    if n_fetched and not (signal_fields["n_attachment_field_present"]
                          or signal_fields["n_thread_field_present"]):
        summary += (
            f" Heads up: none of the {n_fetched} message"
            f"{'s' if n_fetched != 1 else ''} came through with attachment or"
            " conversation details, so the checks that spot an already-sent"
            " deliverable could not run — only the wording of each email was"
            " compared."
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
        # SENTMATCH review F-4 — the same block written to the audit event, so
        # a caller can act on it without re-reading events.jsonl.
        "signal_fields": signal_fields,
        # BUG-3719 capture pass (additive; zeros/None when items not passed).
        "n_opened": n_opened,
        "opened": list(capture["opened"]) if isinstance(capture, dict) else [],
        "capture": capture,
        # MAILSEAM — what this run attributed its refs to, so a caller can act
        # on it without re-reading the audit event.
        "mail_provider": provider,
        "summary": summary,
    }


def apply_roster_complete_closes(workspace_root, *, source_skill: str,
                                 closed_by: str, batch_id=None) -> dict:
    """AUTOAPPLY §4b — close a multi-counterparty commitment whose ENTIRE
    roster has delivered, when every contributing receipt is id-level.

    WHAT commitment_state.mark_partial_received IS PROTECTING, and why it is
    not modified: that writer keeps RECEIPT distinct from CLOSURE — a receipt
    is informational, and the writer refuses to conflate "the last person's
    thing arrived" with "the user is done with this item". That posture is
    correct and the writer is byte-identical after this change. The decision
    simply does not belong in the writer; it belongs in a detector that can
    see the evidence behind every receipt.

    What M's ruling narrows: when the projector stamps
    `all_counterparties_received` AND every contributing
    `commitment_partial_received` names its counterparty by RESOLVED id and
    carries connector evidence, the close is CORROBORATED (N independent
    receipts, §2) and REVERSIBLE (`commitment_close` → `reopen_commitment`,
    the reverser shipped long before this). One evidence-free receipt in the
    set — a bare manual claim — and the item renders its confirm row exactly
    as it does today.

    Untouched by this: MC1 (never whole-close on a single transcript
    mention), SUB1 D3 (open sub-items block — `close_commitment`'s own guard
    plus the projection stamp), and the pending_review floor.

    LB2 auto lifecycle (FB-20): propose(tier="auto") + close + resolve in one
    iteration; no auto proposal ever rests open. Returns
    {"closed": [...], "skipped": [...], "errors": [...]}."""
    from brain_proposals import propose, resolve_proposal
    from commitment_parties import (all_counterparties_received,
                                    receipts_are_id_level)
    from commitment_state import close_commitment
    from cru_match import load_open_commitments, load_events_defensively
    from cru_match import _commitment_id as _cid
    from cru_match import parent_blocks_auto_resolve

    # Bug #102, same class as the orchestrator's abort: `closed_by` is the
    # resolved primary user. Unresolved, this writes real closure events
    # stamped `resolved_by: None` — irreversible-looking rows attributed to
    # nobody. The whole point of the resolver is that a caller never guesses,
    # so an absent answer is a stop, not a value to write.
    if not closed_by:
        msg = ("apply_roster_complete_closes ABORTED: closed_by is unresolved "
               "(Bug #102) — a closure must name who closed it.")
        print(msg, file=sys.stderr)
        raise PrimaryUserUnresolvedError(msg)

    ws = Path(workspace_root)
    events_path = ws / "_hq" / "data" / "events.jsonl"
    out: dict = {"closed": [], "skipped": [], "errors": []}
    if not events_path.exists():
        return out
    # §7 — ONE batch per RUN, timestamped, matching the efb_/idr_/pbs_
    # precedents. A per-SKILL constant grouped every roster-complete close
    # this skill ever applied into ONE undoable batch, so a single `undo`
    # reached back across days and reopened closes the user never saw
    # (review F-2); `resolve_batch` applies no time window by design.
    batch_id = batch_id or ("rcc_" + _dt.datetime.now(_dt.timezone.utc)
                            .strftime("%Y%m%dT%H%M%SZ"))
    # The narrating surface needs the ref it is advertising "say undo" for.
    out["batch_id"] = batch_id
    events, _skipped = load_events_defensively(events_path)

    # F-28 post-review (F-2): the workspace goes to the LOADER too, not only to
    # the predicate below. Otherwise this function's own gate and the projection
    # stamp the chase surface reads disagree — the closer would close an item
    # the chase had already rendered invisible, and the skip reason below
    # ("renders the confirm row unchanged") would be describing a row gated on a
    # stamp nothing set.
    for c in load_open_commitments(events_path, workspace_root=ws):
        d = c.get("data") if isinstance(c.get("data"), dict) else {}
        cid = _cid(c)
        # F-28: `workspace_root` is what lets the roster reader see that one
        # person written as an id AND that person's name is ONE counterparty.
        # Without it, an item whose id leg WAS receipted still showed a phantom
        # name leg outstanding, so this predicate stayed False forever and the
        # item sat in purgatory — the id leg receipted, the phantom leg
        # unreachable (a name-only leg can never receive a receipt by design).
        # This is the un-sticking seam.
        if not all_counterparties_received(c, workspace_root=ws):
            continue
        if d.get("pending_review"):
            out["skipped"].append({"commitment_id": cid,
                                   "why": "pending_review"})
            continue
        if parent_blocks_auto_resolve(c):
            out["skipped"].append({"commitment_id": cid,
                                   "why": "open sub-items (SUB1 D3)"})
            continue
        ok, n = receipts_are_id_level(events, cid,
                                      commitment_seq=c.get("seq"))
        if not ok:
            out["skipped"].append({
                "commitment_id": cid,
                "why": f"{n} receipt(s), not all id-level — renders the "
                       "confirm row unchanged"})
            continue
        predicate = f"roster_complete:{n}_receipts"
        try:
            res = propose(
                ws, kind="commitment_review",
                fingerprint=f"roster_close:{cid}",
                evidence=f"{predicate} — every counterparty delivered, each "
                         "receipt id-level",
                action_tuples=[{"action": "confirm"}, {"action": "hold"}],
                tier="auto", change_class="commitment_close",
                detector=source_skill,
                render_line=(f"Closed {(d.get('title') or '')[:80]!r} — "
                             "everyone delivered"),
                extra={"commitment_id": cid, "auto_predicate": predicate},
            )
            if res.get("status") != "proposed":
                out["skipped"].append({"commitment_id": cid,
                                       "why": res.get("status")})
                continue
            closed = close_commitment(
                ws, cid, resolved_by=closed_by,
                evidence=predicate, source_skill=source_skill,
                extra_data={"auto_predicate": predicate,
                            "brain_batch_id": batch_id,
                            "brain_change_class": "commitment_close"},
            )
            resolve_proposal(ws, res["proposal_id"], "applied",
                             resolved_by=source_skill,
                             source_skill=source_skill)
            out["closed"].append({"commitment_id": cid,
                                  "status": closed.get("status"),
                                  "n_receipts": n, "predicate": predicate})
        except Exception as exc:  # loud per-item, contained per-run
            out["errors"].append({"commitment_id": cid,
                                  "error": f"{type(exc).__name__}: {exc}"})
    return out


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

    MAILSEAM item 8 — EXCEPT when the audit says `status: "blocked"`: the Sent
    read never happened, so the zero means nothing, and ok=False carries the
    recorded reason. Without this the blocked audit would be read back as a
    healthy empty run, which is the exact dead rail the blocked status exists
    to expose (a clean audit over a skipped read).
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
    # Kept BYTE-FOR-BYTE parallel with reconcile_inbound_commitments.
    # validate_inbound_reconcile_ran — the two are copy-clones by design.
    events, _skipped = load_events_defensively(events_path, since_ts=None)
    for e in events:
        if e.get("type") == "sent_reconcile":
            latest = e  # append-ordered → keep the last one seen
    if latest is None:
        return {"ok": False, "ran": False,
                "reason": "no sent_reconcile audit event — reconciliation did not actually run"}
    d = latest.get("data") or {}
    if d.get("status") == "blocked":
        return {"ok": False, "ran": False,
                "reason": ("the Sent read did not happen — "
                           + (d.get("blocked_reason") or "recorded as blocked")),
                "cursor_from": d.get("cursor_from"),
                "cursor_to": d.get("cursor_to"),
                "sent_scanned_count": d.get("sent_scanned_count"),
                "n_closed": d.get("n_closed")}
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
           "apply_roster_complete_closes", "validate_reconcile_ran",
           "PrimaryUserUnresolvedError"]


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
        # F-28 — a shell caller can supply the workspace so the roster reader
        # reaches the entity graph here too; absent, the raw union as before.
        workspace_root=payload.get("workspace_root"),
    )
    print(json.dumps(out, indent=2))
