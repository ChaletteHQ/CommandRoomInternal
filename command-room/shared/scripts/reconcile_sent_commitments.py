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

# TZDATE3 — canonical date localizer (tz.py, hoisted TZDATE2). Guarded: a
# stripped install missing tz.py keeps the pre-TZDATE3 UTC-slice behavior
# exactly (matches the render_*.py precedent).
try:
    from tz import localize_date as _localize_date  # noqa: E402
except ImportError:
    def _localize_date(ts: str | None, workspace_path: str | None = None) -> str:
        return ts[:10] if isinstance(ts, str) and ts else ""


# FS-11 (M ruling 2026-07-15): auto-close MODERATE-confidence sent-mail matches
# too — the CEO said twice "if they are closed, just close them." Only
# multi-candidate AMBIGUITY (one send that plausibly fulfills more than one open
# commitment) stays a confirm proposal; every unambiguous moderate match closes
# automatically, narrated in the change feed with an `undo` (commitment_close is
# an AUTO_ALLOWED reversible class — the reopen reverser is the safety net).
AUTO_CLOSE_MODERATE = True

# CONTACT1 round 3 — read from the module that owns the rule so the number in
# the sentence the CEO reads can never drift from the number the code enforces.
try:
    from contact_capture import MAX_DEFER_ATTEMPTS as CONTACT_GIVE_UP_TRIES
except Exception:  # pragma: no cover — the contact pass is optional
    CONTACT_GIVE_UP_TRIES = 3

# TTL for the ambiguous confirm proposals that DO stay queued — an unconfirmed
# commitment_review_proposed older than this is RETRACTED instead of
# accumulating. POLICY1-A D8/D15: ONE policy constant (was a private 14 here
# and nothing at all on the transcript rail); the retract itself is the
# review-expiry job's second leg.
from commitment_policy import PROPOSAL_TTL_DAYS as REVIEW_PROPOSAL_TTL_DAYS  # noqa: E402


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


def _clock_now(workspace_root=None):
    """CLOCK1 - the corroborated UTC instant this module stamps from.

    Swaps the CLOCK SOURCE only: every window, cutoff, threshold and output
    format around it is unchanged. A machine clock that has not synced used to
    write its own wrong reading straight into the permanent record; this reads
    the same clock, cross-checked against the newest timestamp the workspace
    already holds. Falls back to the raw machine clock if the helper is
    unavailable, so a stamp can never fail for want of corroboration.

    `workspace_root` is threaded in wherever the calling function already
    has one, because a helper that has to GUESS which workspace it is in
    guesses wrong exactly when it matters: a fire's early phases run in
    their own subprocesses, before anything has registered a root.
    """
    try:
        from trusted_now import trusted_now_utc

        return trusted_now_utc(workspace_root)
    except Exception:
        import datetime as _clock_dt

        return _clock_dt.datetime.now(_clock_dt.timezone.utc)


def _empty_signal_fields() -> dict:
    """The SENTMATCH observability block (review F-4), zeroed.

    `n_fetched` is the denominator — every message the fetch handed over,
    counted BEFORE any fence drops one, so it never shrinks when a fence
    fires. `n_scored` is how many of those actually reached the matcher; the
    `*_field_present` counts say whether the FETCH carried the field at all on
    those, and the `n_with_*` counts say how many messages actually had one.
    The customer-facing dead-rail caveat is measured over `n_scored`, because
    a message a fence dropped never met the delivery checks and so says
    nothing about whether they could run (SELFMAIL1 review F-1). The basis counters close the loop from field to
    outcome. Read together they answer the one question a healthy-looking zero
    cannot: did the delivery checks RUN, or was there nothing to find?

    GRADED AND CLOSED ARE TWO DIFFERENT MOMENTS, AND THE NAMES SAY WHICH.
    Live seq 9561 and 9617 both carried `n_closed_on_delivery: 1` beside
    `n_closed: 0` — read literally, one commitment closed on delivery evidence
    and zero commitments closed. Neither number was wrong; they were measured
    on opposite sides of the write and labelled as though they were measured
    together. `reconcile_sent` grades what it is about to RETURN;
    `reconcile_and_receipt` then writes through `close_commitments`, which
    refuses a `pending_review` item (the floor doing its job) and drops an
    unresolvable id rather than writing an orphan tombstone, and REBINDS
    `auto_close` to what survived. So:

      * `n_graded_on_*` — what the MATCHER proposed. Filled by `reconcile_sent`.
      * `n_closed_on_*` — what was WRITTEN. Filled by `reconcile_and_receipt`
        from the post-rebind list, so it can never outrun `n_closed`.
      * `n_graded_close_refused` + `close_refusals` — how many graded closes the
        closure path refused, and why, keyed by the exception class it raised.
        The delta between the two halves then explains itself instead of
        leaving a reader to infer a lost write from a discrepancy.

    BOTH HALVES ARE KEPT ON PURPOSE. Recomputing `n_closed_on_*` alone would
    answer this defect by re-creating the one F-4 was built for: a run that
    graded a delivery close and had it refused would read identically to a run
    where the delivery checks never ran.

    EVORDER adds `n_stale_evidence_skipped` — candidates dropped because the
    message predates the commitment it would have closed. A fence that drops
    silently is how F-11 stayed invisible for a week, so layer 3 counts.
    """
    return {
        "n_fetched": 0,
        # SELFMAIL1 fix round 1 (review F-1) — how many of those messages were
        # actually SCORED. The two differ by whatever the fences dropped, and
        # the customer-facing dead-rail caveat hangs off THIS one: a caveat
        # about what the delivery checks could see has to be measured over the
        # messages they were allowed to see.
        "n_scored": 0,
        "n_attachment_field_present": 0,
        "n_with_attachment": 0,
        "n_thread_field_present": 0,
        "n_with_thread_ref": 0,
        # Pre-write: the matcher's grades.
        "n_graded_on_delivery": 0,
        "n_graded_on_thread": 0,
        # Post-write: what the closure path actually wrote.
        "n_closed_on_delivery": 0,
        "n_closed_on_thread": 0,
        "n_graded_close_refused": 0,
        "close_refusals": {},
        "n_stale_evidence_skipped": 0,
        # SELFMAIL1 — messages addressed only to the user, dropped before
        # scoring, and rows the send never went to, dropped after. Both are
        # the fence working; both are counted because a fence that drops
        # silently is how F-11 stayed invisible for a week.
        "n_self_addressed_skipped": 0,
        "n_not_addressed_skipped": 0,
    }


def _short_date(ts, workspace_path=None):
    """'2026-05-31T14:00:00' → '2026-05-31'. Defensive — return '' on junk.

    TZDATE3 — localizes via `tz.localize_date` when a workspace path is
    passed (the evidence/summary lines are chat-facing prose, so an
    evening-local send must not read as tomorrow's UTC date).
    `workspace_path=None` (the default) keeps the pre-TZDATE3
    raw-UTC-slice behavior for any caller that hasn't been updated."""
    if not isinstance(ts, str) or not ts:
        return ""
    if workspace_path:
        return _localize_date(ts, workspace_path)
    return ts[:10]


def reconcile_sent(
    open_commitments,
    sent_messages,
    *,
    user_person_id,
    provider=None,
    exclude_captured_since=None,
    workspace_root=None,
    from_mail: bool = True,
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

    No clock, and the caller still emits the events + persists the cursor.
    NOT pure any more, and the docstring says so rather than letting a reader
    find out: when `workspace_root` is passed, SELFMAIL1 asks the contact pass
    for the user's own addresses and name tokens — two read-only reads of the
    entity graph, once per run, wrapped so they can never raise into the loop.
    `workspace_root=None` keeps the old contract exactly: no reads at all, and
    the self-addressed test falls back to the resolved-id channel, which needs
    nothing from disk.

    `from_mail` (CLOSETRUTH1 fix round 2, review F-13) — WHAT KIND OF EVIDENCE
    THIS BATCH IS, stated by the caller, not guessed from a field. M's
    SENT-MAIL ruling puts a proof floor on the bare-title path: a mail whose
    only claim is that its subject echoes a row has to carry a completion
    signal or an attachment before it closes anything. The floor is the MAIL
    rail's; a message a person typed in chat is that person's own word, which
    the 2026-09-07 ruling already treats as evidence, and no ruling has put a
    second bar on that door.

    Round 1 derived the fact from `msg.get("subject") is not None`. That read
    is WRONG AGAINST THIS MODULE'S OWN INPUT SHAPE above: `subject` is
    `str|None`, so a mail is contractually allowed to arrive with a null
    subject — and such a mail slipped the floor entirely and closed a weak
    title match with no proof at all. The kind of evidence is a property of
    the RAIL the batch came down, not of one optional field on one message, so
    the rail says it: mail by default (every existing caller unchanged), and
    `from_mail=False` from the chat seam, which is the one non-mail caller.
    Both the matcher-level gate (`require_title_proof`) and the caller-level
    FS-11 promotion read this one value, so the two can no longer disagree.
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

    # SELFMAIL1 — the contact pass's own-address rule, asked once per run.
    # `contact_capture` OWNS this rule (bar 2's `own_address` refusal: "a
    # message to yourself is not correspondence with anyone"); this rail
    # simply asks it. No workspace on hand → both sets are empty and the
    # address and name channels go inert, leaving the resolved-id channel to
    # do the work — the same honest degrade `own_addresses` already makes.
    # F-7 — through the one helper, so both legs of this rail resolve the
    # user's identity the same way and neither can drift into doing it per
    # message.
    own_addrs, own_toks = (_own_identity(workspace_root)
                           if workspace_root is not None else (set(), set()))
    try:
        from contact_capture import is_self_addressed as _is_self_addressed
    except Exception:  # pragma: no cover
        _is_self_addressed = None

    for msg in sent_messages or []:
        if not isinstance(msg, dict):
            continue
        ts = msg.get("ts")
        if isinstance(ts, str) and (cursor_ts is None or ts > cursor_ts):
            cursor_ts = ts
        # SELFMAIL1 fix round 1 (review F-1) — `n_fetched` IS THE DENOMINATOR
        # and it counts every message the fetch handed over, INCLUDING the ones
        # a fence drops below. Counted here, at the top, exactly where the
        # sibling inbound rail counts it: a message the rail refused is still a
        # message the rail read, and a denominator that shrinks when a fence
        # fires makes every ratio built on it lie. (Built the other way first,
        # and the receipt then told the CEO his mail connector had stopped
        # carrying attachment and conversation details on a night whose only
        # message was a note he wrote to himself.)
        signals["n_fetched"] += 1

        # SELFMAIL1 layer 1 — a note the user mailed to themselves is never
        # evidence of anything. Counted as READ (`n_fetched` above) and dropped
        # before SCORING, so it is neither delivery evidence nor a title match
        # nor a thread prior: the row is never created rather than
        # created-then-suppressed, exactly like RECONFENCE layer 1 above. (Fix
        # round 2, review R-3: this said "dropped BEFORE `n_fetched`", which was
        # true of the first cut and became false the moment F-1 moved the
        # denominator above this block — a comment contradicting the line it
        # sits on is how the next reader puts the bug back.) The CURSOR still
        # advances over it — the message was genuinely read, and re-reading it
        # forever would be a different bug. (Regression: 2026-09-07, an
        # empty-subject note to himself closed two real promises and queued
        # four proposals.)
        if _is_self_addressed is not None and _is_self_addressed(
                msg, user_person_id=user_person_id,
                own_addresses=own_addrs, own_name_tokens=own_toks):
            signals["n_self_addressed_skipped"] += 1
            continue

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
        # These stay BELOW the fences, unlike `n_fetched` above: they answer
        # "could the delivery checks run on the messages we actually scored?",
        # so they are measured over the scored set — the same split the inbound
        # rail makes between `n_fetched` and `n_scored`.
        signals["n_scored"] += 1
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
            # SELFMAIL1 layer 2 — the user's own name tokens, so the title
            # route cannot read the SENDER's own name in a title as "this send
            # went to the counterparty". Empty → the route behaves exactly as
            # it did before, which is what every other caller gets.
            sender_name_tokens=own_toks,
            # CLOSETRUTH1 3.1 — the title-path proof floor is M's SENT-MAIL
            # ruling, so it applies to mail and only to mail. The question
            # half of the gate always applies, at every door.
            #
            # Fix round 2 (review F-13): the kind is the BATCH's, declared by
            # the caller (`from_mail`), never inferred from this message's
            # subject. A mail is allowed a null subject by this module's own
            # INPUT SHAPE contract, and inferring from it let exactly that
            # mail close a bare title echo with no proof.
            require_title_proof=from_mail,
        )
        # CLOSETRUTH1 3.1 — the matcher's own fulfillment finding for THIS
        # message, computed once and carried onto every proposal it makes.
        # It is what the `pending_review` writer stamps instead of the `None`
        # it used to pass (see `has_completion_signal=` below): a confirm the
        # bulk-accept fence can weigh needs to know whether the send said the
        # work was done, and this rail can answer that.
        from cru_match import (detect_completion_signal as _detect_completion,
                               is_question_shaped as _is_question_shaped)
        _msg_text = (msg.get("subject") or "") + " " + (msg.get("body") or "")
        _msg_completion = bool(_detect_completion(_msg_text))
        _msg_question = bool(_is_question_shaped(_msg_text))
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
                    + (f" ({_short_date(ts, workspace_root)})" if _short_date(ts, workspace_root) else "")
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
                # CLOSETRUTH1 3.1 — the three message-level findings the
                # proof gate below reads.
                "has_completion_signal": _msg_completion,
                "question_shaped": _msg_question,
                "has_attachment": bool(msg.get("has_attachment")),
                # CLOSETRUTH1 fix round 1 (review F-4) — the same "is this
                # caller mail?" fact the matcher is handed as
                # `require_title_proof`, carried onto the proposal so the
                # FS-11 promotion below can read it too. Without it the
                # scoping existed at one level and not the other, and the
                # chat door ended up stricter on a WEAK match than on a
                # strong one.
                #
                # Fix round 2 (review F-13): ONE derivation now, the batch's
                # declared kind, shared with the `require_title_proof=` above.
                # A subject-less mail is still a mail and still owes proof.
                "from_mail": from_mail,
                "evidence": (
                    lede
                    + (f" \"{msg.get('subject')}\"" if msg.get("subject") else "")
                    + (f" ({_short_date(ts, workspace_root)})" if _short_date(ts, workspace_root) else "")
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

    # A commitment with a partial receipt this run leaves pending — the
    # per-person receipt is the more precise record of the same evidence.
    partial = [p for p in partial_by_cid.values() if p["receipts"] or p["skipped_names"]]
    partial_cids = {p["commitment_id"] for p in partial if p["receipts"]}
    # CLOSETRUTH1 3.1 — ONE SEND, ONE ITEM, ACROSS BOTH BANDS.
    #
    # THE TWO HOLES THIS CLOSES (measured 2026-09-13, HOLD driver 1). The
    # 19:53 PT "Agreement" mail matched two open rows for the same
    # counterparty and closed BOTH: (i) the 1:1 rule was applied to
    # `pending_all` only, so two rows at or above the auto bar off ONE message
    # both closed unchallenged; (ii) `_msg_counts` was counted over the
    # pending band alone, so a row already sitting in `auto_close` did not
    # count as a sibling and the moderate one beside it was promoted as
    # "unambiguous" — the very ambiguity FS-11 exists to catch.
    #
    # THE RULE NOW, IN TWO PARTS, because the two holes are two decisions:
    #
    #   (i) A message that puts TWO ROWS AT CLOSE GRADE closes NEITHER. Both
    #       reach the person as proposals (the skill's own doctrine —
    #       `reconcile-sent/SKILL.md`: "which one did the send actually
    #       fulfill?"). That is the measured defect: two rows at or above the
    #       auto bar off one mail, both closed, neither kept.
    #
    #   (ii) The moderate promotion counts BOTH BANDS. A row already closing
    #       off this message is a sibling, so the moderate one beside it is
    #       not "unambiguous" and is not promoted.
    #
    # WHY THE FIRST TALLY IS NOT OVER EVERY PROPOSAL (found by the replay on
    # the copy of the book, 2026-09-14). A real message to a real counterparty
    # draws a dozen weak confirm proposals simply because it is addressed to
    # someone a dozen rows are owed to. Counting those as siblings would stop
    # every genuine delivery close on a busy book: the replay's delivery mail
    # went from one close to none. A row that was never going to close is not
    # a competing answer to "which one did this send fulfill" — it is noise,
    # and the person already sees it as a confirm.
    from collections import Counter as _Counter
    _msg_key = lambda p: (p.get("message_id") or f"__nomid_{p['commitment_id']}")
    _close_grade_counts = _Counter(
        _msg_key(p) for p in best.values()
        if p["recommendation"] == "auto_resolve")
    _msg_counts = _Counter(_msg_key(p) for p in best.values())
    auto_close = [p for p in best.values()
                  if p["recommendation"] == "auto_resolve"
                  and _close_grade_counts[_msg_key(p)] == 1]
    # The auto-grade rows one message put on two open items. They become
    # proposals rather than closes — named, so the receipt can say why.
    _one_send_many = [
        dict(p, recommendation="pending_review", one_send_many_items=True,
             evidence=("one send, more than one open item — "
                       + (p.get("evidence") or "matched an outbound send")))
        for p in best.values()
        if p["recommendation"] == "auto_resolve"
        and _close_grade_counts[_msg_key(p)] > 1
        and p["commitment_id"] not in partial_cids
    ]
    pending_all = [
        p for p in best.values()
        if p["recommendation"] == "pending_review"
        and p["commitment_id"] not in partial_cids
    ] + _one_send_many
    # FS-11: promote UNAMBIGUOUS moderate matches to auto-close. Ambiguity = one
    # sent message that matched more than one open commitment at moderate grade
    # (which one did the send actually fulfill? — keep those for confirm). A
    # moderate match that is 1:1 with its send is closed, flagged `moderate` so
    # the feed narrates it honestly ("probably handled — undo if not").
    #
    # CLOSETRUTH1 3.1 — and the promotion obeys the SAME proof floor as the
    # auto band. A moderate title match is the THINNEST evidence this rail
    # has; promoting one on a message that reported nothing and carried
    # nothing is the bare title echo M's ruling 2 names, one band lower. A
    # row whose match already stands on real evidence (`close_basis` — a
    # delivery or a reply on the thread) keeps its promotion.
    def _proved(p) -> bool:
        # `has_completion_signal` is THREE-STATE (True / False / not assessed),
        # and a truthiness read of it is the bug G-completion-truthiness
        # exists to stop: `None` means "nobody judged", which must never read
        # as "no". So it is compared by identity, and the attachment — a plain
        # boolean — is compared on its own.
        # F-4: the floor is M's SENT-MAIL ruling, and it is scoped here to
        # the same callers the matcher scopes it to. A message typed in chat
        # is the customer's OWN word, which M's 2026-09-07 ruling treats as
        # evidence in itself; demanding a completion signal on top of it
        # would be a second bar for the one door that already has the
        # person's say-so. Absent key = mail, so every existing caller is
        # unchanged.
        return (not p.get("from_mail", True)
                or bool(p.get("close_basis"))
                or p.get("has_completion_signal") is True
                or bool(p.get("has_attachment")))

    pending = []
    for p in pending_all:
        if (AUTO_CLOSE_MODERATE and _msg_counts[_msg_key(p)] == 1
                and not p.get("one_send_many_items")
                and not p.get("question_shaped") and _proved(p)):
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
    # FS-11 promotion, so this is the matcher's FINAL grade rather than an
    # intermediate one — but it is still a GRADE, and the names say so. Nothing
    # has been written at this point: this function does no I/O, and the rows
    # counted here still have to survive `close_commitments` in the caller
    # (the pending-review floor and the unresolvable-id refusal both take rows
    # out of the list after this line runs). `n_closed_on_*` is the caller's to
    # fill from what it actually wrote — see `_empty_signal_fields`.
    for p in auto_close:
        if p.get("close_basis") == _DELIVERY_BASIS:
            signals["n_graded_on_delivery"] += 1
        elif p.get("close_basis") == _THREAD_BASIS:
            signals["n_graded_on_thread"] += 1

    # EVORDER — fold layer 3's drop count into the receipt. A non-zero value is
    # the fence working, not an error: it says "N candidates were older than
    # their own evidence and were refused."
    signals["n_stale_evidence_skipped"] = int(
        _evorder_diag.get("stale_evidence_dropped", 0))
    # SELFMAIL1 layer 2's count, folded the same way: rows the matcher graded
    # and then refused because the send never went to the person the row is
    # owed to. Non-zero is the fence working.
    signals["n_not_addressed_skipped"] = int(
        _evorder_diag.get("not_addressed_dropped", 0))

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


def _write_cursor(workspace_root, raw, new_cursor, *, source_skill,
                  contact_cursor=None):
    """Persist workspace.sent_reconcile_cursor, preserving the wrapper shape.
    Uses the locked JSON writer (concurrent-writer safe).

    CONTACT1: `raw` is RE-READ from disk here rather than trusted from the
    caller. The contact-capture pass creates PEOPLE in this same file, and a
    doc snapshotted before those creates would write them straight back out —
    a silent clobber of the very records the fire just made. `new_cursor=None`
    leaves the sent cursor untouched, so a fire that advanced only the contact
    cursor still lands one write.

    `contact_cursor` (also optional) rides the SAME write —
    `contact_capture.stamp_contact_cursor` sets the key; one fire, one
    entities.json write, no interleaving."""
    from atomic_write import atomic_write_json_locked
    # Deliberately ignore the caller's snapshot — see the docstring. The
    # parameter stays in the signature so every existing call site is
    # unchanged and the intent stays readable at those sites.
    _cur, raw = _read_cursor(workspace_root)
    inner = raw["entities"] if isinstance(raw.get("entities"), dict) else raw
    ws = inner.setdefault("workspace", {})
    if not isinstance(ws, dict):
        ws = {}
        inner["workspace"] = ws
    if new_cursor is not None:
        ws["sent_reconcile_cursor"] = new_cursor
    if contact_cursor is not None:
        from contact_capture import stamp_contact_cursor

        stamp_contact_cursor(raw, contact_cursor)
    atomic_write_json_locked(_entities_path(workspace_root), raw, holder=source_skill)


def _record_blocked_run(workspace_root, events_path, cursor_before, *,
                        reason, source_skill, fired_via, provider=None,
                        triggered_by=None) -> dict:
    """MAILSEAM item 8 — the receipt for a fire whose Sent READ could not run.

    Writes a `sent_reconcile` audit event stamped `status: "blocked"` with the
    reason, leaves the cursor exactly where it was, and returns a receipt in
    the same shape as a real run so callers need no new branch. The audit is
    still written — a blocked fire that leaves NO trace is indistinguishable
    from a fire that never happened, and the whole point of this event is that
    a validator can tell those apart. `validate_reconcile_ran` reads the status
    and refuses it, so a blocked run can never be reported as a clean zero."""
    from atomic_write import atomic_append_jsonl as _append
    from cru_match import _now_iso as _audit_ts

    # No hand-stamped seq (BUG-8330 item 7) — appender allocates in-lock.
    audit_event = {
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
        # SCHED1 — the shared stamp helper, so the machine token and its
        # not-persisted flag land the same way here as on every other receipt.
        from receipts import machine_fields

        audit_event["data"].update(machine_fields())
    except Exception:
        pass
    # SPEC_NIGHTM3_LANES §5 P-2, fix pass 1 — which surface asked, only when
    # one did (a run nobody named keeps the audit it always had).
    if triggered_by:
        audit_event["data"]["triggered_by"] = triggered_by
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
        # CONTACT1 — a blocked fire read nothing, so it captured no contacts
        # and moved no contact cursor either. Same shape as a real run so
        # callers need no new branch.
        "n_contacts_added": 0,
        "n_contacts_gave_up": 0,
        "contacts_added": [],
        "contacts": None,
        "mail_provider": provider,
        "summary": summary,
    }

# =============================================================================
# ATTRIB1-B D9 — DOOR 2: the user's own recap confirms a pending capture.
# =============================================================================
#
# `reconcile_sent` matches sent mail to OPEN commitments and closes them. It
# never looked at the PENDING captures — the extractor's unconfirmed guesses
# — so the user's own recap of a meeting ("as discussed, I'll send you the
# deck Friday") could not confirm the very row it restates. This leg does
# exactly that, and ONLY that: a sent message to a meeting attendee inside
# `OWN_RECAP_WINDOW_HOURS` of the capture, whose text matches a pending
# capture of that meeting at the existing matcher's auto-resolve grade
# (threshold unchanged), CONFIRMS it — `clear_review_flags` with
# `confirmed_by: own_recap`. It never closes: a recap restating a promise is
# evidence the promise is real, not evidence it is done.
#
# Self-contained by design (night 8: POLICY1-A routes the CLOSE leg through
# `decide` in the same file — this leg touches none of that).

# Hours after the capture inside which a sent message reads as the recap of
# that meeting (D9 / DD-8: "within 48h of the meeting").
OWN_RECAP_WINDOW_HOURS = 48
OWN_RECAP_CONFIRMED_BY = "own_recap"
OWN_RECAP_NOTE = "confirmed by your own recap to an attendee of this meeting"


def _parse_ts(value):
    """ISO → aware UTC datetime, or None."""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def _meeting_parties_index(workspace_root) -> dict:
    """source-ref key → set of resolved person ids on the `meeting` event
    (top-level `person_ids`, plus `data.attendees` emails resolved against
    the entity graph). Reads through `events_io.iter_events` — the
    canonical shard-aware iterator (INDEX1 D5)."""
    index: dict = {}
    try:
        from events_io import iter_events
        from meeting_capture import (_load_people, _person_emails,
                                     meeting_ref_keys)
    except Exception:  # pragma: no cover
        return index
    by_email: dict = {}
    for p in _load_people(workspace_root):
        for e in _person_emails(p):
            by_email.setdefault(e, p["id"])
    try:
        for ev in iter_events(workspace_root):
            if ev.get("type") != "meeting":
                continue
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            keys = meeting_ref_keys(d.get("source_ref"))
            if not keys:
                continue
            ids = {p for p in (ev.get("person_ids") or []) if isinstance(p, str)}
            for addr in (d.get("attendees") or []):
                pid = by_email.get(str(addr or "").strip().lower())
                if pid:
                    ids.add(pid)
            for k in keys:
                index.setdefault(k, set()).update(ids)
    except Exception:  # pragma: no cover — degrade to an empty index
        return {}
    return index


def _own_identity(workspace_root):
    """SELFMAIL1 — the user's own addresses and own name tokens, resolved
    ONCE. Both are full reads of the entity graph, so a caller resolves them
    before its message loop and hands them down, exactly as `reconcile_sent`
    does. Read-only; never raises into a caller. A workspace that cannot
    answer returns two empty sets, which leaves the address and name channels
    inert and the resolved-id channel doing the work."""
    try:
        from contact_capture import own_addresses, own_name_tokens
    except Exception:  # pragma: no cover — the contact pass is optional
        return set(), set()
    try:
        return (own_addresses(workspace_root) or set(),
                own_name_tokens(workspace_root) or set())
    except Exception:  # pragma: no cover
        return set(), set()


def _self_addressed(msg, user_person_id, workspace_root, own=None) -> bool:
    """SELFMAIL1 — `contact_capture.is_self_addressed` with this rail's two
    workspace reads done for it.

    `own` is the `(addresses, name tokens)` pair from `_own_identity`, hoisted
    out of the caller's loop. Review F-7: without it this resolved both sets
    per MESSAGE — three `entities.json` parses and a connector-config read on
    every message in the batch — where the sent rail correctly resolves them
    once per run. Omitted, it still resolves them itself, so no caller is
    broken by the added argument."""
    if own is None:
        own = _own_identity(workspace_root)
    addrs, toks = own
    try:
        from contact_capture import is_self_addressed
    except Exception:  # pragma: no cover — the contact pass is optional
        return False
    return is_self_addressed(msg, user_person_id=user_person_id,
                             own_addresses=addrs, own_name_tokens=toks)


def confirm_pending_captures(workspace_root, sent_messages, *, user_person_id,
                             provider=None, source_skill="morning-briefing",
                             pending_rows=None, now_iso=None) -> dict:
    """DOOR 2 (D9 / DD-8). Returns `{"n_confirmed", "confirmed":
    [{commitment_id, title, ts, message_id}], "n_candidates"}`.

    Candidates: pending meeting captures (a `commitment` row with
    `pending_review` whose source is a meeting) where SOME recipient of the
    sent message is a resolved party of that meeting, and the send lands
    inside `OWN_RECAP_WINDOW_HOURS` AFTER the capture (a send before the
    capture is not its recap — the EVORDER rule, restated). Match: the
    existing `match_send_to_commitments` at its auto-resolve grade — the
    threshold is not touched. On match: `clear_review_flags(...,
    confirmed_by="own_recap")`, pointer = the sent message's own key.

    Never closes. Never writes when `user_person_id` is empty. Idempotent
    across a day: a row confirmed once is no longer pending and drops out
    of the candidate set by itself."""
    out = {"n_confirmed": 0, "confirmed": [], "n_candidates": 0}
    if not user_person_id or not sent_messages:
        return out
    ws = Path(workspace_root)
    if pending_rows is None:
        from cru_match import load_needs_review
        pending_rows = load_needs_review(
            str(ws / "_hq" / "data" / "events.jsonl"), workspace_root=str(ws))
    from meeting_capture import meeting_ref_keys

    # F-11 — the index is a SECOND full-history pass, so it is built only
    # when there is a pending meeting capture to build it for. A run with
    # nothing pending (the common one) now costs nothing.
    meeting_rows = [ev for ev in (pending_rows or [])
                    if meeting_ref_keys((ev.get("data") or {}).get(
                        "source_ref"))]
    if not meeting_rows:
        return out
    parties = _meeting_parties_index(workspace_root)
    from commitment_state import CommitmentIdError, clear_review_flags

    # Pending MEETING captures only, keyed by their meeting's parties.
    candidates: list = []
    for ev in meeting_rows:
        d = ev.get("data") or {}
        keys = meeting_ref_keys(d.get("source_ref"))
        who: set = set()
        for k in keys:
            who |= parties.get(k, set())
        if not who:
            continue
        cap_ts = _parse_ts(ev.get("ts"))
        if cap_ts is None:
            continue
        candidates.append((ev, who, cap_ts))
    out["n_candidates"] = len(candidates)
    if not candidates:
        return out

    window = _dt.timedelta(hours=OWN_RECAP_WINDOW_HOURS)
    confirmed_ids: set = set()
    # SELFMAIL1 fix round 1 (review F-3) — WHICH NAME IS THE CEO'S. This leg
    # called the matcher without it, so layer 2's title route here could be
    # satisfied by the CEO's own name: a row titled "<CEO> to send the pricing
    # deck" and a note to himself whose recipient name is his own graded
    # `auto_resolve` on this call shape and `no_action` on the sent rail's,
    # from identical input. Row titles carrying the owner's own name are
    # ordinary on extraction, and this leg has no layer 2 behind it. Resolved
    # once, before the loop, exactly as `reconcile_sent` resolves it.
    # F-7 — resolved ONCE, here, not once per message. Both halves are full
    # reads of the entity graph and the loop below used to redo them for every
    # message in the batch.
    own_identity = _own_identity(workspace_root)
    own_toks = own_identity[1]
    try:
        from confidence import match_score_auto_resolve
        auto_threshold = float(match_score_auto_resolve(workspace_root))
    except Exception:  # pragma: no cover — the shipped constant
        from confidence import MATCH_SCORE_AUTO_RESOLVE
        auto_threshold = float(MATCH_SCORE_AUTO_RESOLVE)
    for msg in sent_messages or []:
        if not isinstance(msg, dict):
            continue
        send_ts = _parse_ts(msg.get("ts"))
        if send_ts is None:
            continue
        recips = {r for r in (msg.get("recipient_person_ids") or [])
                  if isinstance(r, str) and r}
        if not recips:
            continue
        # SELFMAIL1 — the same door, on the same rail. The user sits in his own
        # meetings, so `who & recips` is satisfied by a note he mailed to
        # himself, and this leg would confirm a pending capture off it. A recap
        # to yourself is not a recap to an attendee.
        if _self_addressed(msg, user_person_id, workspace_root,
                           own=own_identity):
            continue
        opens: list = []
        for ev, who, cap_ts in candidates:
            if _commitment_id(ev) in confirmed_ids:
                continue
            if not (who & recips) or not (cap_ts <= send_ts <= cap_ts + window):
                continue
            # The matcher's recipient gate wants the recipient among the
            # row's people. A pending capture is pending precisely because
            # its counterparty is unresolved, so the MEETING supplies the
            # party: the recipients who sat in that meeting are added to a
            # shallow COPY's `person_ids` for scoring only — the substrate
            # row is untouched, and the title/body score still decides.
            copy = dict(ev)
            copy["person_ids"] = sorted(
                set(ev.get("person_ids") or []) | (who & recips))
            opens.append(copy)
        if not opens:
            continue
        mid = str(msg.get("message_id") or "").strip()
        own_key = primary_artifact_key(provider, mid) if mid else None
        results = match_send_to_commitments(
            open_commitments=opens,
            sender_person_id=user_person_id,
            recipient_person_ids=list(recips),
            subject=msg.get("subject"),
            body=msg.get("body"),
            recipient_names=msg.get("recipient_names") or [],
            # F-3 — the same argument the sent rail passes. Absent, the CEO's
            # own name in a row's title stands in for the counterparty.
            sender_name_tokens=own_toks,
            send_source_ref=own_key,
            send_ts=msg.get("ts"),
            workspace_root=workspace_root,
        )
        for r in results:
            # The matcher DEMOTES an auto-resolve-grade match on a pending
            # row to `pending_review` — a floor against CLOSING a guess. This
            # leg never closes, so it reads the GRADE: the existing
            # auto-resolve threshold, untouched, per workspace.
            if (r.get("score") or 0) < auto_threshold:
                continue
            cid = str(r.get("commitment_id") or "")
            if not cid or cid in confirmed_ids:
                continue
            try:
                res = clear_review_flags(
                    workspace_root, cid, cleared_by=user_person_id,
                    source_skill=source_skill, note=OWN_RECAP_NOTE,
                    source_ref=own_key, mint_now_iso=now_iso,
                    confirmed_by=OWN_RECAP_CONFIRMED_BY)
            except CommitmentIdError:
                continue
            if res.get("status") != "cleared":
                continue
            confirmed_ids.add(cid)
            out["confirmed"].append({"commitment_id": cid,
                                     "title": r.get("title") or "",
                                     "ts": msg.get("ts") or "",
                                     "message_id": mid})
    out["n_confirmed"] = len(out["confirmed"])
    return out


def _effective_fired_via(explicit):
    """`receipts.effective_fired_via`, behind an import that cannot break (the
    `chat_reconcile._effective_fired_via` shape).

    SPEC_NIGHTM3_LANES §5 P-2, fix pass 1 (review H-2; ruling R-RW-5). This
    composer defaulted `fired_via="scheduled"`, so the reconcile-sent job a
    typed brief runs on a merged seat recorded a scheduled fire nobody
    claimed. Off a merged seat the resolver answers today's `scheduled`, so
    the un-merged fleet is byte-identical; an explicit value always wins."""
    try:
        from receipts import effective_fired_via
    except Exception:  # noqa: BLE001 - a resolver that raises is worse
        return explicit if explicit is not None else "scheduled"
    try:
        return effective_fired_via(explicit)
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"


def _asked_by(explicit=None):
    """The surface that asked for this run — the argument, else
    `CR_TRIGGERED_BY` (log_receipt's own rule) — or None. Never raises."""
    import os

    raw = explicit if explicit else os.environ.get("CR_TRIGGERED_BY")
    raw = str(raw or "").strip()
    return raw or None


#: MAINTJOBS1 MUST 2 - the fields a Sent message carries into the writer, in
#: the order Step 2 of `skills/reconcile-sent/SKILL.md` names them. The plan
#: hands them to the fire's connector step so the rows it builds are the rows
#: this module scores.
SENT_MESSAGE_FIELDS = ("message_id", "ts", "thread_id", "has_attachment",
                       "recipient_person_ids", "recipient_names",
                       "recipient_emails", "subject", "body")

#: How far back a first run (no `sent_reconcile` audit yet) reads, and the
#: overlap a normal run keeps behind its cursor (Step 1, Bug #101).
FIRST_RUN_DAYS = 30
CURSOR_OVERLAP_DAYS = 1


def plan_sent_window(workspace_root, now=None) -> dict:
    """The reconcile-sent job PLANNED beside the data - READ ONLY (MAINTJOBS1
    MUST 2; on `workspace_access.RUN_HELPER_ALLOWLIST`).

    Answers what the fire's connector step needs and nothing it has to guess:
    the cursor it will be validated against, the Sent window (Step 1's three
    branches collapse to two on a fire: a first run reads `FIRST_RUN_DAYS`,
    a normal run reads from one day behind the cursor), the provider-neutral
    fetch intent (`{"in_sent": true, "after": <date>}`, compiled per provider
    by `connector_adapters/mail.py`), the declared provider, the host tool
    searches that load the mail connector under its display name, the primary
    user the owner gate needs, the message fields to build, and the fire
    start the circularity fence is keyed on. `ready` is false - and
    `blocked_reason` says why in plain words - when no primary user resolves:
    the writer would refuse, so the fire does not fetch.

    Writes nothing: no cursor, no audit, no sidecar."""
    # CLOCK1 (MAINTJOBS1 review, v5.33.0 merge-fix): the corroborated clock,
    # never the raw machine clock, decides the window.
    now = now or _clock_now(workspace_root)
    if isinstance(now, str):
        now = _dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.astimezone()
    try:
        cursor, _raw = _read_cursor(workspace_root)
    except Exception:  # noqa: BLE001 - an unreadable book plans a first run
        cursor = None
    try:
        first_run = not validate_reconcile_ran(workspace_root).get("ran")
    except Exception:  # noqa: BLE001
        first_run = True
    floor = now - _dt.timedelta(days=FIRST_RUN_DAYS)
    if cursor and not first_run:
        try:
            cur = _dt.datetime.fromisoformat(str(cursor).replace("Z", "+00:00"))
            if cur.tzinfo is None:
                cur = cur.replace(tzinfo=_dt.timezone.utc)
            floor = cur - _dt.timedelta(days=CURSOR_OVERLAP_DAYS)
        except ValueError:
            first_run = True
    after = floor.date().isoformat()
    try:
        provider = resolve_mail_provider(workspace_root, None)
    except Exception:  # noqa: BLE001
        provider = None
    try:
        from primary_user import resolve_primary_user
        user = resolve_primary_user(workspace_root)
    except Exception:  # noqa: BLE001
        user = None
    try:
        from inbox_helpers import connector_discovery_queries
        queries = list(connector_discovery_queries(workspace_root))
    except Exception:  # noqa: BLE001
        queries = []
    return {
        "job_id": "reconcile-sent",
        "cursor_before": cursor,
        "first_run": bool(first_run),
        "after": after,
        "intent": {"in_sent": True, "after": after},
        "provider": provider,
        "connector_queries": queries,
        "user_person_id": user,
        "message_fields": list(SENT_MESSAGE_FIELDS),
        "fire_start": now.astimezone(_dt.timezone.utc).isoformat(),
        "ready": bool(user),
        "blocked_reason": (None if user else
                           "the workspace's own person is not recorded"),
    }


def reconcile_and_receipt(
    workspace_root,
    sent_messages,
    *,
    user_person_id,
    source_skill="morning-briefing",
    outcome_watch_summary=None,
    fired_via=None,
    sent_commitment_items=None,
    provider=None,
    exclude_captured_since=None,
    fetch_blocked=None,
    contact_capture_items=None,
    triggered_by=None,
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
        "n_contacts_added": int,     # CONTACT1 pass (0 when not run)
        "contacts_added": [ {email, name, person_id, message_id, org_id} ],
        "contacts": dict|None,       # full capture_contacts receipt
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

    `contact_capture_items` (SPEC CONTACT1) — contact candidates the skill
    extracted from the SAME Step-2 fetch: one per (sent message x direct To/CC
    recipient), shape per `contact_capture.capture_contacts`. When not None,
    this run also AUTO-CREATES contact records for people the CEO
    demonstrably corresponds with — through `people_writer.auto_add_person`
    (FS-11) on the existing `person_org_creation_structured_fact` auto class,
    behind the two-way + hard-clarity gate, applied-then-narrated with undo.
    It rides its OWN cursor (`workspace.contact_capture_cursor`), NOT the sent
    one: reconcile-sent deliberately fetches wide windows regardless of the
    sent cursor (Bug #101, "catch up my sent mail"), which is safe for
    idempotent closures and would be a 30-day silent backfill here. None (the
    default) = pre-CONTACT1 behavior, byte-identical.

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

    `fired_via` omitted: the SEAT answers (`receipts.effective_fired_via`) —
    `scheduled` on every un-merged seat, byte for byte; on a merged VM seat
    the forwarded `CR_FIRED_VIA`, else `manual`. `triggered_by` names the
    surface that asked (else `CR_TRIGGERED_BY`), written on the audit event
    only when present (SPEC_NIGHTM3_LANES §5 P-2, fix pass 1).
    """
    # MAINTJOBS1 MUST 2 - THE WRITER IS NAMED FIRST. This function closes
    # commitments, captures promises, adds contacts and moves a cursor before
    # its audit row is written, and the audit row's own stamp refuses nothing.
    # On the write door the door asks this question too; a shell import on a
    # merged seat with no forwarded identity now refuses HERE, in the one
    # sentence, before any of those writes.
    from receipts import require_writer_identity

    require_writer_identity(workspace_root=workspace_root)
    fired_via = _effective_fired_via(fired_via)
    asked_by = _asked_by(triggered_by)
    if not user_person_id:
        msg = (
            "reconcile-sent ABORTED: the primary user is unresolved "
            "(resolve_primary_user returned None/empty). Every owner gate "
            "would match nothing and this run would write a clean audit "
            "claiming zero to close. No audit event written, cursor NOT "
            "advanced. Fix: pass the WORKSPACE ROOT (not _hq) to "
            "resolve_primary_user, or set workspace.user_id in "
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
            provider=provider, triggered_by=asked_by,
        )

    # BUG-8330 item 15 — ONE lock session for the whole fire. This function
    # used to run N+1 separate lock cycles per fire (the closes, each partial
    # receipt, each pending-review append, each auto-added contact's
    # entities.json write, then the cursor), and every release under a
    # cloud-sync hold could mv-aside a fresh piece of lock litter — the
    # helper built for exactly this (multi_write_context) was used by two
    # backfill scripts and not by the rail that fires daily. Concurrent
    # fires now serialize up front instead of interleaving lock churn;
    # inner writers keep their own per-file locks (reentrant-safe).
    from atomic_write import multi_write_context
    with multi_write_context(workspace_root, holder=source_skill):
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
        # POLICY1-A D8 — the close leg passes through `decide` at the one
        # table, BEFORE the writer: the sent rail's own grades are its
        # signals (title at the bar, SENTMATCH delivery, FS-11 unambiguous
        # moderate), a row policy will not close (a pending target, a
        # bar-less row) joins the propose list carrying its reason. Nothing
        # that closed before is refused here — the bars are the rail's own,
        # named — and nothing new closes.
        if auto_close:
            from commitment_policy_pass import policy_gate_closes
            from cru_match import _is_pending_review as _pending_flag
            _pending_ids = {str(_commitment_id(c)) for c in opens if _pending_flag(c)}
            auto_close, _demoted = policy_gate_closes(
                auto_close, rail="sent", pending_ids=_pending_ids,
                workspace_root=workspace_root)
            for _row in _demoted:
                pending.append(_row)
                signal_fields["n_graded_close_refused"] += 1
                refusals = signal_fields["close_refusals"]
                _why = _row.get("policy_refusal") or "PolicyGate"
                refusals[_why] = refusals.get(_why, 0) + 1
        if auto_close:
            from commitment_state import close_commitments
            results = close_commitments(
                workspace_root,
                [{
                    "commitment_id": c["commitment_id"],
                    "resolved_by": "sent_reconcile",
                    "evidence": c.get("evidence") or "matched an outbound send",
                    "primary_thread_id": c.get("primary_thread_id") or "",
                    # PROV1 — the sent message that IS the fulfillment. Built
                    # through `primary_artifact_key`, the same key the matcher
                    # above compared on, so the close cites the artifact it
                    # matched rather than a second spelling of it. An
                    # unresolved provider degrades to the legacy anchor there
                    # and here identically; a row with no message id at all
                    # lands marked rather than blocked.
                    "source_ref": primary_artifact_key(
                        provider, c.get("message_id")),
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
                if r.get("status") == "error":
                    # NAME THE REFUSAL. Every one of these is the system working
                    # — the pending-review floor, the orphan-tombstone refusal,
                    # the sub-item guard, the malformed-pointer guard — and
                    # every one of them used to leave the receipt reporting a
                    # close that never landed. Keyed by the exception class, so
                    # a fifth refusal added to `close_commitments` shows up here
                    # without an edit.
                    reason = str(r.get("error") or "UnknownError")
                    refusals = signal_fields["close_refusals"]
                    refusals[reason] = refusals.get(reason, 0) + 1
                    signal_fields["n_graded_close_refused"] += 1
            auto_close = [c for c in auto_close if str(c["commitment_id"]) in closed_or_already]

        # THE POST-WRITE COUNT (live seq 9561/9617). `auto_close` above is now
        # what SURVIVED the closure path, so the basis counters are recomputed
        # from it rather than inherited from the matcher's pre-write grade. This
        # is what makes `n_closed_on_delivery` unable to outrun the `n_closed`
        # sitting beside it in the same audit event; the matcher's numbers are
        # still here under `n_graded_on_*`, which is what keeps "graded and
        # refused" distinguishable from "the checks never ran".
        for c in auto_close:
            if c.get("close_basis") == _DELIVERY_BASIS:
                signal_fields["n_closed_on_delivery"] += 1
            elif c.get("close_basis") == _THREAD_BASIS:
                signal_fields["n_closed_on_thread"] += 1

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
                            # PROV1 — the send that delivered to THIS
                            # counterparty. A roster close later stands on
                            # these receipts, so each one has to name its own
                            # message or the derived close is unauditable.
                            source_ref=primary_artifact_key(
                                provider, r.get("message_id")),
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
                    # CLOSETRUTH1 3.1 — ASSESSED. This used to pass `None`
                    # ("the caller could not judge"), which was true only
                    # because nobody had asked: the sent matcher reads the
                    # same completion phrases the transcript rail does, and
                    # since 3.1 it reads them on every message it scores. The
                    # bulk-accept fence weighs a confirm by what the evidence
                    # actually said, and a question-shaped mail that reports
                    # nothing must not screen as strong. `None` survives only
                    # for a proposal minted before this field existed.
                    has_completion_signal=(
                        p["has_completion_signal"]
                        if isinstance(p.get("has_completion_signal"), bool)
                        else None),
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

        # SPEC CONTACT1 — auto contact records for people the CEO actually
        # corresponds with. Runs AFTER the capture pass and BEFORE the cursor
        # write, because it is the one phase that writes PEOPLE into
        # entities.json: the cursor write below re-reads the doc for exactly that
        # reason. Per-item gate refusals are normal outcomes, not errors; a
        # malformed item lands loudly in contacts["errors"] and never crashes a
        # fire whose closes have already landed.
        contacts = None
        if contact_capture_items is not None:
            from contact_capture import capture_contacts

            try:
                contacts = capture_contacts(
                    workspace_root,
                    contact_capture_items,
                    source_skill=source_skill,
                    # The orchestrator owns the single entities.json write.
                    write_cursor=False,
                )
            except Exception as exc:  # a contact pass must never sink a reconcile
                print(f"contact capture failed: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                contacts = {"ran": False, "error": f"{type(exc).__name__}: {exc}",
                            "n_added": 0, "n_needs_confirm": 0, "n_carried": 0,
                            "n_refused": 0, "n_skipped": 0, "n_errors": 0,
                            "n_queue_rows_retired": 0, "added": [],
                            "cursor_from": None, "cursor_to": None}

        # Advance the cursor to the newest Sent ts we saw (never backwards).
        cursor_after = cursor_before
        new_ts = res.get("cursor_ts")
        sent_cursor_write = None
        if new_ts and (cursor_before is None or new_ts > cursor_before):
            cursor_after = new_ts
            sent_cursor_write = cursor_after
        contact_cursor_write = None
        if isinstance(contacts, dict) and contacts.get("ran") and \
                contacts.get("cursor_to") and \
                contacts.get("cursor_to") != contacts.get("cursor_from"):
            contact_cursor_write = contacts["cursor_to"]
        if sent_cursor_write is not None or contact_cursor_write is not None:
            _write_cursor(workspace_root, raw, sent_cursor_write,
                          source_skill=source_skill,
                          contact_cursor=contact_cursor_write)

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
        from atomic_write import atomic_append_jsonl as _append
        from cru_match import _now_iso as _audit_ts
        # No hand-stamped seq (BUG-8330 item 7) — appender allocates in-lock.
        # ATTRIB1-B D9 — DOOR 2. After the close leg and the proposals, and
        # self-contained: the user's own recap CONFIRMS a pending capture of
        # the meeting it restates; it never closes one.
        recap = confirm_pending_captures(
            workspace_root, sent_messages or [], user_person_id=user_person_id,
            provider=provider, source_skill=source_skill)
        n_recap = int(recap.get("n_confirmed") or 0)

        audit_event = {
            "ts": _audit_ts(),
            "type": "sent_reconcile",
            "source_skill": source_skill,
            "data": {
                # v4.5.2 R1 receipt-contract fields (shared/RECEIPT_CONTRACT.md).
                "task_id": "reconcile-sent",
                "kind": "reconcile-sent",
                "status": "complete",
                # ATTRIB1-B D9 — pending captures confirmed by the user's own
                # recap this run (never closed).
                "n_confirmed_by_recap": n_recap,
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
            # SCHED1 — the shared stamp helper, so the machine token and its
            # not-persisted flag land the same way here as everywhere else.
            from receipts import machine_fields

            audit_event["data"].update(machine_fields())
        except Exception:
            pass
        # SPEC_NIGHTM3_LANES §5 P-2, fix pass 1 — which surface asked for this
        # run, only when one did: a run nobody named keeps the audit it always
        # had (the legacy control is byte-identical).
        if asked_by:
            audit_event["data"]["triggered_by"] = asked_by
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
        # CONTACT1 (D2, additive): the contact pass's counts ride the SAME receipt
        # — one fire, one verifiable trace. Absent fields = a pre-CONTACT1 run or
        # items not passed. `n_contacts_carried` is here so the cap is HONEST: a
        # capped fire that reported only what it created would be indistinguishable
        # from a fire that found nothing more to do.
        if isinstance(contacts, dict):
            audit_event["data"]["n_contacts_added"] = contacts.get("n_added", 0)
            audit_event["data"]["n_contacts_needs_confirm"] = \
                contacts.get("n_needs_confirm", 0)
            audit_event["data"]["n_contacts_carried"] = contacts.get("n_carried", 0)
            audit_event["data"]["n_contacts_refused"] = contacts.get("n_refused", 0)
            audit_event["data"]["n_contacts_skipped"] = contacts.get("n_skipped", 0)
            audit_event["data"]["n_contacts_errors"] = contacts.get("n_errors", 0)
            audit_event["data"]["n_contact_queue_rows_retired"] = \
                contacts.get("n_queue_rows_retired", 0)
            audit_event["data"]["contact_cursor_from"] = contacts.get("cursor_from")
            audit_event["data"]["contact_cursor_to"] = contacts.get("cursor_to")
            # CONTACT1 round 3 — the two states that were previously invisible
            # fleet-wide. A RESET is the changelog's own promise ("it resets to
            # today and adds nobody for that gap"), and a promise nothing records
            # cannot be checked on a customer's machine. A GIVE-UP means the pass
            # stopped retrying an address it could not write; without it, three
            # identical stuck fires and three quiet healthy ones look the same.
            audit_event["data"]["n_contacts_gave_up"] = contacts.get("n_gave_up", 0)
            if contacts.get("cursor_reset"):
                audit_event["data"]["contact_cursor_reset"] = \
                    contacts["cursor_reset"]
        _append(events_path, [audit_event])

        # SELFMAIL1 fix round 1 (review F-1). `n_scored` is how many of the
        # fetched messages actually reached the matcher; `n_fetched` still
        # counts every message that was read. The two differ only when a fence
        # dropped something, and both sentences below hang off the difference.
        n_scored = int(signal_fields.get("n_scored", n_fetched))
        n_self_skipped = int(signal_fields.get("n_self_addressed_skipped", 0))
        if n_fetched == 0:
            summary = (f"No new sent mail since {_short_date(cursor_before, workspace_root) or 'the last check'} "
                       f"— nothing to reconcile.")
        elif n_auto == 0 and n_self_skipped and not n_scored:
            # THE WHOLE BATCH WAS MAIL HE WROTE TO HIMSELF. Say that, instead
            # of "nothing matched an open commitment" — which is true and
            # useless, and reads as though the mail was examined and came up
            # empty. Same one sentence, not one more: a fence that fired must
            # not cost the surface a line. (The sibling inbound rail has said
            # its version of this — "they were all from you" — since REPLYCLOSE.)
            summary = (
                f"Checked {n_fetched} sent message"
                f"{'s' if n_fetched != 1 else ''} — "
                + ("it went" if n_fetched == 1 else "they all went")
                + " only to you, so there was nothing to check "
                + ("it" if n_fetched == 1 else "them")
                + " against. A note to yourself can't show a promise kept.")
        elif n_auto == 0:
            summary = (f"Checked {n_fetched} sent message{'s' if n_fetched != 1 else ''} "
                       f"— nothing matched an open commitment.")
        else:
            tail = f", {n_pend} to confirm" if n_pend else ""
            summary = (f"Reconciled your sent mail through {_short_date(cursor_after, workspace_root)}: "
                       f"closed {n_auto} you'd already handled{tail}.")
        n_opened = capture["n_opened"] if isinstance(capture, dict) else 0
        if n_opened:
            summary += (f" Started tracking {n_opened} new "
                        f"promise{'s' if n_opened != 1 else ''} from your sent mail.")
        if n_recap:
            # D9 / DD-8 — the receipt line, verbatim from the spec.
            summary += (f" {n_recap} capture{'s' if n_recap != 1 else ''} "
                        f"confirmed by your own recap.")
        n_contacts = contacts.get("n_added", 0) if isinstance(contacts, dict) else 0
        if n_contacts:
            summary += (
                f" Added {n_contacts} "
                f"{'contact' if n_contacts == 1 else 'contacts'} you've been "
                f"emailing back and forth with — say `undo` to reverse.")
            n_carry = contacts.get("n_carried", 0)
            if n_carry:
                summary += (
                    f" {n_carry} more are waiting their turn and will be added on "
                    f"the next pass.")
        # These two say themselves even on a fire that added nobody — which is
        # exactly the fire they describe. A stall that only shows up in an audit
        # event is a stall nobody finds.
        n_gave_up = contacts.get("n_gave_up", 0) if isinstance(contacts, dict) else 0
        if n_gave_up:
            summary += (
                f" {n_gave_up} contact{'s' if n_gave_up != 1 else ''} couldn't be "
                f"added after {CONTACT_GIVE_UP_TRIES} tries — "
                f"{'they' if n_gave_up != 1 else 'that person'} will be picked up "
                f"again on their next message.")
        if isinstance(contacts, dict) and contacts.get("cursor_reset"):
            summary += (
                " Contact-adding started fresh from today — the record of where it "
                "had reached wasn't usable, so nothing older was read back.")
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
        # SELFMAIL1 fix round 1 (review F-1) — MEASURED OVER WHAT WAS SCORED,
        # not over what was fetched. A message the self-addressed fence dropped
        # never reached the delivery checks, so it can neither prove nor
        # disprove that they could run; counting it here is how a night whose
        # only message was a note the CEO wrote to himself came out as "your
        # mail is not carrying attachment or conversation details" — an alarm
        # about a connector that was working perfectly, and the one sentence
        # this whole lane ADDED to a customer surface. `n_scored` is the
        # denominator; on a run that fenced nothing it equals `n_fetched` and
        # this sentence is byte-identical to what it always was.
        # SELFMAIL1 fix round 2 (review R-4) — ONE DENOMINATOR ON THE SURFACE.
        # When a fence dropped nothing, `n_scored` IS the count the sentence
        # above already gave and the caveat says it, word for word as it always
        # has. When a fence DID drop something the two numbers differ, and
        # printing the second one beside the first ("Checked 2 sent messages …
        # none of the 1 message came through") makes the reader do arithmetic
        # nobody explained: both numbers are true and the difference between
        # them is a fence they were never told about. So the caveat drops its
        # own count and names its set in words instead — the count on the
        # surface stays the one the reader was given.
        if n_scored and not (signal_fields["n_attachment_field_present"]
                             or signal_fields["n_thread_field_present"]):
            if n_scored == n_fetched:
                scope = (f"none of the {n_scored} message"
                         f"{'s' if n_scored != 1 else ''}")
            else:
                scope = "none of the mail we could check"
            summary += (
                f" Heads up: {scope} came through with attachment or"
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
            # ATTRIB1-B D9 — door 2 (additive).
            "n_confirmed_by_recap": n_recap,
            "confirmed_by_recap": list(recap.get("confirmed") or []),
            # BUG-3719 capture pass (additive; zeros/None when items not passed).
            "n_opened": n_opened,
            "opened": list(capture["opened"]) if isinstance(capture, dict) else [],
            "capture": capture,
            # CONTACT1 (additive; zeros/None when items not passed).
            "n_contacts_added": n_contacts,
            "n_contacts_gave_up": n_gave_up,
            "contacts_added": list(contacts["added"]) if isinstance(contacts, dict)
                              and contacts.get("added") else [],
            "contacts": contacts,
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
    batch_id = batch_id or ("rcc_" + _clock_now(workspace_root)
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
                # PROV1 — this close is derived from the ACCUMULATED per-person
                # receipts, not from one message, so its pointer is the gate
                # run that read them. Each underlying receipt carries its own
                # message pointer (mark_partial_received), so the chain back to
                # mail stays walkable one hop down.
                source_ref=f"session:{batch_id}",
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
