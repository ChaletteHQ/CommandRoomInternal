#!/usr/bin/env python3
"""
Inbound-mail commitment capture — the missing half of the email leg
(v5.12.1, INCAP1).

WHY THIS EXISTS
---------------
A counterparty could promise something in writing, with a date — Command Room
read the message, summarized it in the widget, drafted the reply — and tracked
NOTHING. Capture builders existed for meetings (`meeting_capture`), the user's
own sent mail (`sent_capture`) and Slack (`slack_capture`); the inbox
orchestrator's phase list had no capture phase at all, and there was no
inbound-mail capture builder for it to call. Two orchestrators already fenced
against captures this lane was assumed to make routinely — both cite the
`<provider>:<message_id>` stamp "on exactly those captures" — so the prose
contract shipped years before the writer. All-time, the triage rail had written
4 commitment events.

Downstream that absence is structural, not cosmetic. `thread_ref` had exactly
one writer (`sent_capture`), reachable only from the user's OWN sent mail, so
the owner of every thread-anchored item was always the user. REPLYCLOSE's reply
bases require `owner == the inbound sender`, so the population they read could
not exist by construction — waiting-on items with a thread anchor, ever: 0.
This module is that writer.

TWO DIRECTIONS, ONE LANE
------------------------
1. `DIRECTION_WAITING_ON` — the SENDER promised the user something ("I owe you
   the corrected invoices"). Owner is the sender; the user is the counterparty.
   This is the owed-to-you population REPLYCLOSE reads.
2. `DIRECTION_REPLY_OWED` — an inbound ask the user is expected to ANSWER: a
   warm intro, a direct question, a document request. Owner is the USER, the
   sender is the counterparty, and the item is always confirm-tier because the
   obligation is INFERRED — the user has not accepted it yet. Before INCAP1 the
   direction table skipped this shape outright ("not a commitment until
   accepted"), which is why the highest-frequency thing in a mailbox — a
   message someone is waiting on a reply to — was the one thing never tracked.

DIRECTION DOCTRINE (hard fence): the user's OWN messages never create items
through this lane. A message whose sender resolves to the primary user is
refused loudly, not filtered quietly — the user's outbound promises are
`sent_capture`'s lane, on the sent rail's evidence, and a message that reached
this function claiming to be inbound is an extraction defect worth surfacing.

EVORDER (hard fence): this lane is a WRITER. It must never close, resolve,
supersede, or reschedule anything. `assert_writer_only` re-checks the composed
batch before the append, so the rule survives a future edit that forgets it.
Closure power lives in REPLYCLOSE, on the reconcile rails, reading the rows
this module writes.

Division of labor, same as the sibling legs: the SKILL does the semantic
extraction — it reads the fetched threads, applies the Stage-D capture floor
(owner + deliverable + consequence) and COMMITMENT_SCHEMA.md's extraction
triggers, and decides what is a real commitment and which direction it runs in.
This module does the parts that must be exact:

  1. The capture block — `build_inbound_commitment_event` runs THE shared
     Stage-D / S2 / Stage-E gate (`capture_gate.gate_commitment_data`) every
     capture writer runs, then stamps owner/counterparty from the DIRECTION
     rather than from the extractor's opinion.
  2. Anchor integrity (F-22) — `source_ref` and `thread_ref` are minted from
     the connector's real ids through one speller. A draft-shaped id is refused
     outright: a capture anchored to a draft id can never be matched against
     the message that was actually sent, so it is worse than no anchor.
  3. Cross-channel restatement dedup (`capture_gate.matches_open_commitment`)
     plus per-message idempotency (`already_captured`), so a re-fire over the
     same mailbox writes nothing and a promise already tracked from a meeting
     merges instead of double-tracking.
  4. The relevance gate — items route through W4c's `classify_capture`
     (caution rail > org override > mode), observed-tier items convert via
     `observed_from_commitment_event`.
  5. Volume honesty — an inbox catch-up over months must not spray hundreds of
     items. `DEFAULT_CAPTURE_CAP` bounds what ONE fire writes (CATCHUP1's
     period-cap precedent), and the remainder is COUNTED and named in the
     receipt rather than silently dropped. Nothing is marked captured, so the
     next fire picks the deferred slice up.
  6. The receipt — `n_candidates / n_captured / n_deduped / n_below_bar` are
     what separate "the lane didn't fire" from "there was nothing to capture".
     That distinction is the whole reason the absence went unnoticed for
     months: a dead rail and a quiet mailbox produced the identical receipt.

Events omit `seq` (auto-stamped in the writer lock); `ts` is backdated to the
message's own time (schema: `ts` is when the commitment was MADE). Provenance
is the `<provider>:<message_id>` source_ref, matching the sibling legs.

Callers: the inbox orchestrator's capture phase (`orchestrator-inbox.md`, the
phase between the read and the reconcile pass) and `inbox-triage`'s Extract
Commitments step.

stdlib only; nothing here calls the network.
"""
from __future__ import annotations
try:
    from text_clip import clip  # noqa: E402
except ImportError:  # pragma: no cover — direct-path fallback
    import sys as _sys_tc
    from pathlib import Path as _Path_tc
    _sys_tc.path.insert(0, str(_Path_tc(__file__).resolve().parent))
    from text_clip import clip  # noqa: E402

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from capture_gate import (  # noqa: E402
    OBSERVED_TYPE,
    classify_capture,
    gate_commitment_data,
    matches_open_commitment,
    observed_from_commitment_event,
    resolve_capture_mode,
    workspace_capture_context,
)
from connector_adapters.provenance import (  # noqa: E402
    LEGACY_MAIL_PROVIDER,
    is_same_artifact,
    resolve_mail_provider,
)

_TITLE_DEDUP_CHARS = 60  # scan-for-commitments Step 4: first 60 chars, ci

#: The sender promised the user something — owner is the SENDER.
DIRECTION_WAITING_ON = "waiting_on"
#: An inbound ask the user is expected to answer — owner is the USER.
DIRECTION_REPLY_OWED = "reply_owed"
DIRECTIONS = (DIRECTION_WAITING_ON, DIRECTION_REPLY_OWED)

#: What ONE fire may WRITE. Twelve is CATCHUP1's `DEFAULT_PERIOD_CAP`, adopted
#: rather than re-invented: both bound the same failure — a months-deep
#: catch-up dumping a quarter's worth of rows into a surface the CEO reads in
#: the morning. The remainder is deferred, never dropped: nothing is marked
#: captured, so the next fire takes the next slice and the receipt says how
#: many are waiting.
DEFAULT_CAPTURE_CAP = 12

#: Event types this lane is allowed to append. Anything else is a closure or a
#: mutation and belongs to another rail (EVORDER).
WRITER_EVENT_TYPES = frozenset({"commitment", OBSERVED_TYPE})

#: F-22 anchor integrity — a draft id, in every spelling the mail adapters use.
#: Prefix-anchored on purpose: a real connector id that merely CONTAINS the
#: substring ("redraft19") is a real id and must not be refused.
_DRAFT_ID_RE = re.compile(r"(?i)^drafts?[:_\-.]")

#: The confirm-tier reason stamped on every reply-shaped capture. One home, so
#: the surfaces render one sentence rather than a per-fire paraphrase.
REPLY_OWED_REVIEW_REASON = (
    "inferred from an inbound ask — you have not accepted it yet"
)


class InboundItemError(ValueError):
    """An extracted inbound-mail item was malformed — fail loud so a bad
    extraction is visible and goes back to the extractor, never silently
    dropped or silently written wrong (the F-31 bug class; SentItemError /
    SlackItemError's sibling)."""


def assert_writer_only(events) -> None:
    """EVORDER, enforced on the composed batch: raise unless EVERY event is a
    writer type (`WRITER_EVENT_TYPES`).

    The lane's doctrine is that it writes and never closes. That rule is easy
    to state in a docstring and easy to lose in an edit six months from now, so
    it is checked mechanically at the one place a write can leave this module.
    A closure smuggled in from anywhere — a helper that starts returning
    resolutions, an extractor dict that grows a `type` key — is refused before
    the append rather than discovered in the ledger afterwards.
    """
    for ev in events or []:
        etype = (ev or {}).get("type") if isinstance(ev, dict) else None
        if etype not in WRITER_EVENT_TYPES:
            raise InboundItemError(
                f"the inbound capture lane is a WRITER and composed a "
                f"{etype!r} event — this lane must never close, resolve or "
                f"supersede anything (EVORDER). Closure power belongs to the "
                f"reconcile rails, reading the rows this lane writes."
            )
    return None


def _reject_draft(raw: str, what: str) -> None:
    if _DRAFT_ID_RE.match(raw or ""):
        raise InboundItemError(
            f"the inbound capture lane was handed a DRAFT {what} ({raw!r}) — "
            f"captures must carry the connector's real ids (F-22). A row "
            f"anchored to a draft id can never be matched against the message "
            f"that was actually sent, so it is worse than no anchor at all."
        )


def inbound_source_ref(message_id: str, provider: Optional[str] = None) -> str:
    """Provenance ref for an inbound-mail capture: `<provider>:<message_id>` —
    the spelling COMMITMENT_SCHEMA.md reserves for email artifacts, and the
    exact spelling both orchestrators' circularity fences already claim this
    lane stamps. The message id is the connector's canonical per-message id
    (stable across re-fetch), so it is both the dedup anchor and traceable back
    to the message.

    `provider` unresolved falls back to `LEGACY_MAIL_PROVIDER` — the anchor
    every pre-connector-agnostic row on disk already carries, so the two sides
    of a dedup comparison agree in that state. It is a BACK-COMPAT anchor, not
    a preference: `capture_inbound_items`, which has the workspace root in
    hand, resolves the declared email backend first (MAILSEAM item 5), so a
    Superhuman workspace writes its own label even when the skill forgets to
    pass one. Dedup is format-proof either way: `already_captured` compares
    CANONICAL keys, not raw strings.

    Raises `InboundItemError` on an empty id or a draft-shaped one (F-22).
    """
    p = (provider or LEGACY_MAIL_PROVIDER).strip().lower() or LEGACY_MAIL_PROVIDER
    mid = (message_id or "").strip()
    if mid.lower().startswith(p + ":"):
        mid = mid[len(p) + 1:].strip()
    if not mid:
        raise InboundItemError("an inbound-mail capture needs the message id")
    _reject_draft(mid, "id")
    return f"{p}:{mid}"


def _name_is_user(name, user_names) -> bool:
    """True when an UNRESOLVED sender name is the primary user's own.

    Whole-name comparison, case- and whitespace-normalized — deliberately not a
    token test. A token test would refuse every message from anyone who shares
    the user's first name, which on a fresh workspace is a silent capture hole
    exactly where this lane is least able to notice one. The narrow rule
    catches the case that actually happens: a Sent row leaking into an inbox
    fetch, carrying the user's own display name and no person record.
    """
    want = " ".join(str(name or "").lower().split())
    if not want:
        return False
    for candidate in (user_names or ()):
        if " ".join(str(candidate or "").lower().split()) == want:
            return True
    return False


def _parse_iso_date(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        _dt.date.fromisoformat(value.strip()[:10])
        return True
    except ValueError:
        return False


def build_inbound_commitment_event(
    title: str,
    *,
    message_id: str,
    kind: str,
    direction: str,
    user_person_id: str,
    sender_person_id: str = "",
    sender_name: str = "",
    user_names=(),
    ts: str = "",
    due: Optional[str] = None,
    no_due: bool = False,
    evidence: str = "",
    primary_thread_id: Optional[str] = None,
    person_ids: Optional[List[str]] = None,
    classification_confidence: Optional[float] = None,
    pending_review: bool = False,
    review_reason: str = "",
    source_skill: str = "inbox-triage",
    provider: Optional[str] = None,
    thread_id: Optional[str] = None,
    is_draft: bool = False,
) -> dict:
    """One qualifying inbound message → one canonical `commitment` event dict,
    with the full capture block enforced in code (Stage-D kind, S2 due-nudge,
    Stage-E counterparty receipts, the pending_review safety inversion).
    Construction only — append through `event_gate.append_event` (ids minted
    and seq stamped inside the writer lock; C4's semantic dedup fires there).

    `direction` decides attribution, and it is the ONE thing the extractor may
    not be vague about:

      * `DIRECTION_WAITING_ON` — `owner_id` is the SENDER, the user is the
        counterparty. This is the population REPLYCLOSE's reply bases read
        (`commitment_is_waiting_on` requires owner == the inbound sender), and
        before this module it did not exist.
      * `DIRECTION_REPLY_OWED` — `owner_id` is the USER, the sender is the
        counterparty, and `pending_review` is forced TRUE. The obligation is
        INFERRED from someone else's ask, so it is proposed, never asserted:
        an unconfirmed item is excluded from chase and from every closure
        gate until the user adjudicates it. Passing `pending_review=False`
        does not turn that off — absence of the flag is not consent, and here
        the flag is the whole basis on which the row is allowed to exist.

    DIRECTION DOCTRINE: the user's own messages never create items through this
    lane. `sender_person_id == user_person_id` raises — and so does an
    UNRESOLVED sender whose `sender_name` matches one of `user_names`, which is
    the same defect wearing the state every fresh workspace is in. Without that
    second half the fence would be strongest exactly where entities.json is
    fullest and absent where it is empty. The user's own outbound promises are
    `sent_capture`'s lane, on the sent rail's evidence.

    `user_person_id` MUST be a resolved person id, never a guess (Bug #102):
    with no user there is nothing to compare direction against.

    THE SENDER MAY BE UNRESOLVED, and that is deliberate. On a fresh workspace
    entities.json is nearly empty, so requiring a resolved sender would make
    this lane capture NOTHING on exactly the mailboxes that need it most — the
    dead-rail shape this build exists to remove. Pass `sender_name` instead:
    a waiting-on item is then written with an empty `owner_id` and the name in
    `data.owner_external`, which the shared gate reads as "no resolved owner"
    and stamps confirm-tier. The item is visible, attributable to a human by
    name, and waiting on one click to become real — never lost, and never
    silently pretending to be owned. (It cannot close until then:
    `commitment_is_waiting_on` classifies an unowned item OUT. That is the
    honest consequence of not knowing who someone is, not a bug.) At least one
    of `sender_person_id` / `sender_name` is required.

    `thread_id` — the CONVERSATION the message sits in, stamped as
    `data.thread_ref` through the same speller as `source_ref` so the two refs
    canonicalize through one code path. Optional; omitted → no `thread_ref`,
    and the reply bases simply stay inert for that item rather than firing on a
    guess.

    Resolve relative due phrases ("next week", "by Friday") against the
    MESSAGE's date, not the scan date, before calling this.

    Raises InboundItemError on anything the extraction must go back and do.
    """
    title = (title or "").strip()
    if not title:
        raise InboundItemError("an inbound-mail commitment needs a non-empty title")
    if direction not in DIRECTIONS:
        raise InboundItemError(
            f"inbound-mail commitment {title[:40]!r} needs an explicit "
            f"direction, one of {list(DIRECTIONS)} — attribution is decided at "
            f"extraction and never defaulted here (a wrong default writes the "
            f"promise onto the wrong person's plate)"
        )
    if is_draft:
        raise InboundItemError(
            f"inbound-mail commitment {title[:40]!r} was flagged as a draft — "
            f"captures carry the connector's real message ids, never draft "
            f"ids (F-22)"
        )
    source_ref = inbound_source_ref(message_id, provider)
    if not (user_person_id or "").strip():
        raise InboundItemError(
            f"inbound-mail commitment {source_ref} has no resolved user — "
            f"resolve via resolve_primary_user (Bug #102), never guess; "
            f"direction on this lane is a comparison against the user"
        )
    sender_person_id = (sender_person_id or "").strip()
    sender_name = (sender_name or "").strip()
    if not sender_person_id and not sender_name:
        raise InboundItemError(
            f"inbound-mail commitment {source_ref} names no sender at all — "
            f"pass the resolved sender_person_id, or sender_name when the "
            f"sender has no person record yet. Direction is a claim about who "
            f"wrote the message; it cannot be made about nobody."
        )
    if sender_person_id and sender_person_id == user_person_id:
        raise InboundItemError(
            f"inbound-mail commitment {source_ref} names the primary user as "
            f"its SENDER — the user's own messages never create items through "
            f"the inbound lane (direction doctrine). Outbound promises belong "
            f"to sent_capture, on the sent rail's evidence."
        )
    if not sender_person_id and _name_is_user(sender_name, user_names):
        raise InboundItemError(
            f"inbound-mail commitment {source_ref} names an UNRESOLVED sender "
            f"({sender_name!r}) that matches the primary user — direction "
            f"doctrine holds whether or not the roster happens to be populated "
            f"(this is the shape a fresh workspace is in)."
        )

    due_str = (due or "").strip()
    data: dict = {
        "title": title,
        "kind": kind,
        "due": due_str,
        "source_ref": source_ref,
        "channel": "email",
        # Origin discriminator (ACCOUNT_SCOPE §4a): an inbound-mail capture is
        # a connector read — the account-scope wall treats it STRICT.
        "origin": "connector",
        # The parent `interaction` event is not guaranteed to exist for a
        # fetched thread, so — like the sent and Slack legs — the source_ref IS
        # the provenance and there is no source_event_seq.
    }
    if direction == DIRECTION_WAITING_ON:
        # Owner is the sender. `owner_id` is stamped even when empty — that is
        # the shape inbox-triage has always documented for an unrecognised
        # sender ("emit it so it's not lost"), and the shared gate turns the
        # empty into a confirm rather than into a silent guess.
        data["owner_id"] = sender_person_id
        if not sender_person_id:
            data["owner_external"] = sender_name
        data["counterparty_id"] = user_person_id
    else:
        data["owner_id"] = user_person_id
        if sender_person_id:
            data["counterparty_id"] = sender_person_id
        else:
            data["counterparty_name"] = sender_name
    if no_due:
        data["no_due"] = True
    _tid = (thread_id or "").strip()
    if _tid:
        # Same `<provider>:<id>` spelling as source_ref, via the same helper,
        # so a thread never reads as two artifacts.
        data["thread_ref"] = inbound_source_ref(_tid, provider)
    if evidence:
        data["evidence"] = clip(evidence)

    if direction == DIRECTION_REPLY_OWED:
        # Forced, not defaulted: see the docstring. The user did not make this
        # promise — someone else's ask implied it — so it enters the substrate
        # as a proposal and stays out of chase and out of every closure gate
        # until adjudicated.
        pending_review = True
        review_reason = review_reason or REPLY_OWED_REVIEW_REASON
    if pending_review:
        data["pending_review"] = True
        if review_reason:
            data["review_reason"] = review_reason

    # THE shared capture block (v4.6.1 W4c consolidation — one implementation
    # for every writer): Stage-D kind, S2 due-nudge, the promise-vs-task rule,
    # and the pending_review safety inversion (never unsets an extractor-set
    # True, so the forced confirm above survives it).
    gate_commitment_data(
        data,
        subject=f"inbound-mail commitment {source_ref}",
        classification_confidence=classification_confidence,
        error_cls=InboundItemError,
    )

    data["status"] = "open"
    if due_str and _parse_iso_date(due_str):
        if _dt.date.fromisoformat(due_str[:10]) < _dt.datetime.now(
            _dt.timezone.utc
        ).date():
            data["status"] = "overdue"

    # Stage E: both parties are person references — the dual-layer reader links
    # via person_ids, and both surfaces (My Plate / Waiting On) read it.
    pids = [p for p in (person_ids or []) if p]
    for pid in (user_person_id, sender_person_id):
        if pid and pid not in pids:
            pids.append(pid)

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
        event["ts"] = ts.strip()  # backdate to when the message arrived
    return event


def _title_key(title) -> str:
    return (str(title or "").strip().lower())[:_TITLE_DEDUP_CHARS]


def already_captured(workspace_root, message_id: str, title: str,
                     provider: Optional[str] = None) -> bool:
    """Per-message idempotency, codified: True when a commitment with the same
    message identity AND the same title (ci, first 60 chars — the scan's
    documented rule) is already on disk, OR the identity is already covered by
    a closure. Shard-transparent via events_io.

    Reading the closure types here is a READ, not a write: an identity whose
    item the user already closed must not be re-opened by the next fire over
    the same mailbox. That re-open is the specific way an idempotency check
    that only looks at OPEN items fails.

    R16 (connector-agnostic-v1): identity is the CANONICAL dedup key, so a
    legacy-labelled source_ref (any case) and a structured-provenance row for
    the SAME message reduce to one identity. MAILSEAM item 5: `provider`
    unresolved is answered by the workspace's DECLARED email backend, and the
    comparison runs through `is_same_artifact` so EVERY label the same message
    can be on disk under counts.
    """
    try:
        from events_io import iter_events
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(_HERE))
        from events_io import iter_events
    from connector_adapters.provenance import canonical_dedup_key

    provider = resolve_mail_provider(workspace_root, provider)
    want_id = inbound_source_ref(message_id, provider).split(":", 1)[1]
    want_title = _title_key(title)
    for ev in iter_events(workspace_root):
        ev_key = canonical_dedup_key(event=ev)
        if not is_same_artifact(ev_key, provider, want_id):
            continue
        etype = ev.get("type")
        if etype in ("commitment_resolved", "thread_resolved"):
            return True
        if etype == "commitment":
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            ev_title = _title_key(data.get("title") or data.get("summary"))
            if ev_title and want_title and (
                ev_title in want_title or want_title in ev_title
            ):
                return True
    return False


def _summary(counts: dict, cap: int) -> str:
    """The receipt's one plain-English line. Built here so the orchestrator
    cannot paraphrase it differently on each fire."""
    bits = []
    if counts["n_captured"]:
        parts = []
        if counts["n_waiting_on"]:
            parts.append(f"{counts['n_waiting_on']} waiting on them")
        if counts["n_reply_owed"]:
            parts.append(f"{counts['n_reply_owed']} needing your reply")
        detail = f" ({', '.join(parts)})" if parts else ""
        bits.append(f"{counts['n_captured']} tracked from inbound mail{detail}")
    else:
        bits.append("nothing new tracked from inbound mail")
    if counts["n_deduped"]:
        bits.append(f"{counts['n_deduped']} already tracked")
    if counts["n_below_bar"]:
        bits.append(f"{counts['n_below_bar']} below the capture floor")
    if counts["n_capped"]:
        bits.append(
            f"{counts['n_capped']} held for the next pass (this fire writes at "
            f"most {cap})"
        )
    if counts["n_errors"]:
        bits.append(f"{counts['n_errors']} could not be captured")
    return "; ".join(bits) + "."


def capture_inbound_items(
    workspace_root,
    items,
    *,
    user_person_id,
    opens=None,
    source_skill: str = "inbox-triage",
    append: bool = True,
    provider: Optional[str] = None,
    cap: Optional[int] = None,
) -> dict:
    """Run the full inbound-capture pipeline over a batch of SKILL-extracted
    items and (when `append=True`) land the survivors in ONE locked append.

    `items` — extraction dicts from the skill's semantic pass, one per
    trackable thing found in the fetched inbound messages::

        {"message_id", "thread_id", "ts" (the message's own ISO time),
         "direction" (DIRECTION_WAITING_ON | DIRECTION_REPLY_OWED),
         "sender_person_id" (or "sender_name" when the sender has no person
         record yet), "title", "kind", "due" | "no_due": True,
         "evidence", "org_id"/"org_name" (the sender's resolved org, for the
         per-org override), "primary_thread_id", "person_ids",
         "classification_confidence", "pending_review"/"review_reason",
         "below_bar": True/"below_bar_reason" (the extraction's own honest
         declaration that an item did NOT clear the Stage-D floor)}

    Per item, in order: (1) a `below_bar` declaration is COUNTED and never
    written — the counter is what makes "nothing cleared the floor" different
    from "the lane never ran"; (2) `already_captured` — a message already on
    disk is skipped (idempotent re-fires); (3) the capture block via
    `build_inbound_commitment_event`, whose failures land LOUDLY in `errors`,
    never silently dropped and never crashing a batch mid-fire; (4) restatement
    dedup vs the OPEN set and vs earlier items in this batch, with the user
    excluded from the party test (the user is a party to everything on this
    lane, in both directions); (5) W4c relevance routing; (6) the volume cap —
    once this fire has composed `cap` writes, every further survivor is
    DEFERRED and counted. Deferred is not dropped: nothing is marked captured,
    so the next fire takes the next slice.

    `opens` — pass a pre-loaded `load_open_commitments` projection to pin the
    dedup baseline; None loads fresh.

    Returns the receipt::

        {"ran": True, "opened", "merged", "observed", "skipped_existing",
         "below_bar", "capped", "errors",
         "n_candidates", "n_captured", "n_deduped", "n_below_bar", "n_capped",
         "n_opened", "n_merged", "n_observed", "n_skipped", "n_errors",
         "n_waiting_on", "n_reply_owed", "cap", "summary", "events"}

    `n_candidates / n_captured / n_deduped / n_below_bar` are the four the
    inbox fire's receipt carries (§3): together they answer "did the lane fire
    and find nothing, or did it not fire?" — the question nobody could answer
    for the months this lane was missing. `events` carries the built dicts when
    `append=False`.
    """
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    # MAILSEAM item 5 — resolve the provider ONCE for the whole batch, from the
    # declared email backend when the caller passed none, so every ref this
    # batch writes and every key it dedups against carries the same answer.
    provider = resolve_mail_provider(workspace_root, provider)
    if cap is None:
        cap = DEFAULT_CAPTURE_CAP
    cap = max(1, int(cap))
    if opens is None:
        from cru_match import load_open_commitments

        opens = load_open_commitments(str(events_path))

    ctx = workspace_capture_context(workspace_root)
    user_names = ctx.get("user_names") or []

    opened, merged, observed, skipped, errors = [], [], [], [], []
    below_bar, capped = [], []
    batch: list = []
    accepted_open_events: list = []  # in-batch restatement guard
    n_candidates = 0
    n_waiting_on = n_reply_owed = 0

    for item in items or []:
        if not isinstance(item, dict):
            continue
        n_candidates += 1
        mid = item.get("message_id") or ""
        title = item.get("title") or ""
        direction = item.get("direction") or ""

        if item.get("below_bar"):
            below_bar.append({
                "title": title, "message_id": mid,
                "reason": item.get("below_bar_reason") or "below the capture floor",
            })
            continue
        try:
            if already_captured(workspace_root, mid, title, provider=provider):
                skipped.append({"title": title, "message_id": mid})
                continue
            ev = build_inbound_commitment_event(
                title,
                message_id=mid,
                kind=item.get("kind"),
                direction=direction,
                user_person_id=user_person_id,
                sender_person_id=item.get("sender_person_id") or "",
                sender_name=item.get("sender_name") or "",
                # The name half of the direction fence needs the roster the
                # batch already loaded — a pure builder cannot resolve it.
                user_names=user_names,
                ts=item.get("ts") or "",
                due=item.get("due"),
                no_due=bool(item.get("no_due")),
                evidence=item.get("evidence") or "",
                primary_thread_id=item.get("primary_thread_id"),
                person_ids=item.get("person_ids"),
                classification_confidence=item.get("classification_confidence"),
                pending_review=bool(item.get("pending_review")),
                review_reason=item.get("review_reason") or "",
                source_skill=source_skill,
                provider=provider,
                thread_id=item.get("thread_id"),
                is_draft=bool(item.get("is_draft")),
            )
        except InboundItemError as e:
            errors.append({"title": title, "message_id": mid, "error": str(e)})
            continue

        match = matches_open_commitment(
            ev["data"],
            list(opens) + accepted_open_events,
            person_ids=ev.get("person_ids") or (),
            # The user is a party to EVERY item this lane writes, in both
            # directions — so the party test has to be carried by the other
            # side or it would link two unrelated items.
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

        if len(batch) >= cap:
            # Volume honesty: this fire has written its share. The item is NOT
            # marked captured anywhere, so the next fire re-derives it and
            # takes it — and the receipt names how many are waiting.
            capped.append({"title": title, "message_id": mid})
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
        if direction == DIRECTION_WAITING_ON:
            n_waiting_on += 1
        else:
            n_reply_owed += 1
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
                "direction": direction,
                "kind": ev["data"].get("kind"),
                "due": ev["data"].get("due") or "",
                "owner_id": ev["data"].get("owner_id"),
                "pending_review": bool(ev["data"].get("pending_review")),
            })

    # EVORDER — the last thing before the write.
    assert_writer_only(batch)

    if append and batch:
        from event_gate import append_event

        append_event(events_path, batch, holder=source_skill)

    counts = {
        "n_candidates": n_candidates,
        "n_captured": len(opened) + len(observed),
        "n_deduped": len(skipped) + len(merged),
        "n_below_bar": len(below_bar),
        "n_capped": len(capped),
        "n_opened": len(opened),
        "n_merged": len(merged),
        "n_observed": len(observed),
        "n_skipped": len(skipped),
        "n_errors": len(errors),
        "n_waiting_on": n_waiting_on,
        "n_reply_owed": n_reply_owed,
    }
    out = {
        "ran": True,
        "opened": opened,
        "merged": merged,
        "observed": observed,
        "skipped_existing": skipped,
        "below_bar": below_bar,
        "capped": capped,
        "errors": errors,
        "cap": cap,
        "summary": _summary(counts, cap),
    }
    out.update(counts)
    if not append:
        out["events"] = batch
    return out


__all__ = [
    "InboundItemError",
    "DIRECTION_WAITING_ON",
    "DIRECTION_REPLY_OWED",
    "DIRECTIONS",
    "DEFAULT_CAPTURE_CAP",
    "WRITER_EVENT_TYPES",
    "REPLY_OWED_REVIEW_REASON",
    "assert_writer_only",
    "inbound_source_ref",
    "build_inbound_commitment_event",
    "already_captured",
    "capture_inbound_items",
]
