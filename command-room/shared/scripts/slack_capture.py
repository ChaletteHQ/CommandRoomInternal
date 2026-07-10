#!/usr/bin/env python3
"""
Slack commitment capture — deterministic half of the Slack leg (v4.6.0 MC3).

COMMITMENT_SCHEMA.md carried its own TODO for years: "Slack-based commitments
are NOT currently extracted" — a whole channel of promises invisible while the
schema already reserved `slack:<permalink>` source_refs. This module closes
that hole for `scan-for-commitments`: the SKILL does the semantic extraction
(reads channel/DM history via the Slack connector, decides what is a real
commitment); this module does the parts that must be exact:

  1. Message hygiene — `normalize_message` drops channel noise (bot posts,
     join/leave/topic subtypes, deleted messages, empty text) and normalizes
     edits (`message_changed` wrappers unwrap to the LATEST text) so the
     extractor only ever sees real human messages in their current form.
  2. Direction split — `classify_direction` sorts messages into the two
     capture-worthy lanes: the user's OWN sent messages (the primary promise
     source — what I promised) and messages naming the user (the owed-to-you
     source). Third-party↔third-party chatter classifies `other` and the
     builder REFUSES to mint an open item from it (W4c's relevance bound,
     enforced at this leg from day one; the observed tier lands with W4c).
  3. Scope + cost bounds — `within_window` (default 7 days; chat is noisier
     than mail, so the leg's default window is tighter than the scan's 30-day
     meeting/email default) and `cap_messages` (keep newest, REPORT the drop —
     a silent cap reads as "covered everything" when it didn't).
  4. The capture block — `build_slack_commitment_event` runs the SAME
     Stage-D / S2 / Stage-E gate every capture writer now runs (parity with
     scan-for-commitments Step 3 prose and session_sweep._gate_commitment,
     v4.5.2 C1): required `kind` classified at extraction, due-or-no_due with
     silence rejected, task-with-counterparty rejected, counterparty receipts
     joined into person_ids, and the pending_review safety inversion (absence
     of the flag is an assertion of confident attribution, never an accident).
  5. Idempotency — `already_captured` codifies the scan's Step 4 dedup rule
     ((source_ref, title) — case-insensitive first-60-chars) so re-running the
     Slack leg is safe. C4's cross-writer SEMANTIC dedup is not re-implemented
     here — it fires inside the append path (`atomic_append_jsonl` →
     commitment_dedup) for this leg like any other writer.

Events are appended by the SKILL through `event_gate.append_event` (one batch,
one locked call) — builders here are construction-only, same convention as
meeting_capture / cru_match builders. Omit `seq`; `ts` is backdated to the
message's Slack timestamp (schema: ts is when the commitment was MADE).

Connector-down doctrine (T10): tool discovery lives in
`tool_discovery.discover_slack_tool`. No tool = the leg silently doesn't
exist (zero errors, zero mentions); tool present but failing mid-scan = one
honest line and the rest of the scan proceeds. Nothing in this module ever
calls the network. stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from capture_gate import gate_commitment_data  # noqa: E402

# Scope + cost bounds (MC3): chat volume is unbounded, the scan is not.
DEFAULT_WINDOW_DAYS = 7
DEFAULT_MESSAGE_CAP = 400

# Direction lanes. Only the first two may become open commitments.
DIRECTION_USER_SENT = "user_sent"      # the user's own message — what I promised
DIRECTION_NAMES_USER = "names_user"    # someone else's message naming the user
DIRECTION_OTHER = "other"              # third-party↔third-party — never an open item

# Slack message subtypes that are channel noise, never commitment sources.
NOISE_SUBTYPES = frozenset(
    {
        "bot_message",
        "bot_add",
        "bot_remove",
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "channel_archive",
        "channel_unarchive",
        "channel_convert_to_private",
        "channel_convert_to_public",
        "group_join",
        "group_leave",
        "reminder_add",
        "channel_canvas_updated",
        "huddle_thread",
        "message_deleted",
        "tombstone",
    }
)

_TITLE_DEDUP_CHARS = 60  # scan-for-commitments Step 4: first 60 chars, ci


class SlackItemError(ValueError):
    """An extracted Slack item was malformed — fail loud so a bad extraction
    is visible and goes back to the extractor, never silently dropped or
    silently written wrong (the F-31 bug class, SweepItemError's sibling)."""


def slack_source_ref(permalink: str) -> str:
    """Provenance ref for a Slack capture: `slack:<permalink>` — the exact
    spelling COMMITMENT_SCHEMA.md reserves. The permalink is the connector's
    canonical per-message URL (unique per message, stable across edits), so it
    is both the dedup anchor and a click-through the user can open."""
    link = (permalink or "").strip()
    if not link:
        raise SlackItemError("a Slack capture needs the message permalink")
    if link.startswith("slack:"):
        link = link[len("slack:"):].strip()
        if not link:
            raise SlackItemError("a Slack capture needs the message permalink")
    return f"slack:{link}"


def normalize_message(msg) -> Optional[dict]:
    """One raw connector message → the effective human message, or None when
    the message is channel noise the extractor must never see.

    Handles both history shapes in the wild: an edited message returned
    directly (carries an `edited` key, text already current — passes through)
    and the `message_changed` wrapper (nested `message` holds the latest text
    — unwrapped, then re-screened, since the inner message may itself be a
    bot post)."""
    if not isinstance(msg, dict):
        return None
    if msg.get("type") not in (None, "message"):
        return None
    subtype = (msg.get("subtype") or "").strip()
    if subtype == "message_changed":
        inner = msg.get("message")
        if not isinstance(inner, dict):
            return None
        merged = dict(inner)
        for carry in ("channel", "channel_id", "permalink"):
            if msg.get(carry) is not None and merged.get(carry) is None:
                merged[carry] = msg[carry]
        return normalize_message(merged)
    if subtype in NOISE_SUBTYPES:
        return None
    if msg.get("bot_id"):
        return None
    if not (msg.get("text") or "").strip():
        return None
    if not (msg.get("user") or "").strip():
        return None
    return msg


def classify_direction(
    msg: dict,
    *,
    user_slack_ids: Sequence[str],
    user_names: Sequence[str] = (),
) -> str:
    """Which capture lane a (normalized) message belongs to.

    `user_slack_ids` are the workspace owner's Slack member ids (resolved once
    per scan via the connector's user search matched to the primary-user
    entity). `user_names` are display-name fallbacks for plain-text mentions;
    tokens under 3 chars never match (initials false-positive on everything)."""
    ids = {i for i in (user_slack_ids or []) if i}
    author = (msg.get("user") or "").strip()
    if author and author in ids:
        return DIRECTION_USER_SENT
    text = msg.get("text") or ""
    for uid in ids:
        if f"<@{uid}>" in text:
            return DIRECTION_NAMES_USER
    lower = text.lower()
    for name in user_names or ():
        token = (name or "").strip().lower()
        if len(token) >= 3 and re.search(rf"\b{re.escape(token)}\b", lower):
            return DIRECTION_NAMES_USER
    return DIRECTION_OTHER


def _epoch(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def within_window(slack_ts, *, days: int = DEFAULT_WINDOW_DAYS, now=None) -> bool:
    """True iff a Slack epoch timestamp (`"1720476923.000200"`) falls inside
    the last-N-days scan window. Unparseable timestamps are OUT (a message the
    scan cannot place in time is a message it must not bill as recent).
    `now` accepts an aware datetime or epoch float; defaults to real now."""
    t = _epoch(slack_ts)
    if t is None:
        return False
    if now is None:
        now_epoch = _dt.datetime.now(_dt.timezone.utc).timestamp()
    elif isinstance(now, _dt.datetime):
        now_epoch = now.timestamp()
    else:
        now_epoch = float(now)
    if t > now_epoch + 300:  # tolerate small clock skew, not future messages
        return False
    return (now_epoch - t) <= days * 86400


def cap_messages(
    messages: Iterable[dict],
    *,
    cap: int = DEFAULT_MESSAGE_CAP,
) -> Tuple[List[dict], int]:
    """Volume bound: keep the newest `cap` messages (by Slack ts), return
    `(kept, dropped_count)`. The caller MUST surface a non-zero drop in its
    summary — a silently-capped scan reads as full coverage (no-silent-caps
    doctrine)."""
    msgs = [m for m in messages if isinstance(m, dict)]
    if cap is None or cap <= 0 or len(msgs) <= cap:
        return msgs, 0
    ordered = sorted(msgs, key=lambda m: _epoch(m.get("ts")) or 0.0, reverse=True)
    return ordered[:cap], len(msgs) - cap


def _slack_ts_to_iso(slack_ts) -> Optional[str]:
    t = _epoch(slack_ts)
    if t is None:
        return None
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat()


def _parse_iso_date(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        _dt.date.fromisoformat(value.strip()[:10])
        return True
    except ValueError:
        return False


def build_slack_commitment_event(
    title: str,
    *,
    permalink: str,
    kind: str,
    direction: str,
    owner_id: str = "",
    owner_external: str = "",
    due: Optional[str] = None,
    no_due: bool = False,
    counterparty_id: Optional[str] = None,
    counterparty_name: Optional[str] = None,
    counterparty_ids: Optional[List[str]] = None,
    counterparty_names: Optional[List[str]] = None,
    evidence: str = "",
    channel: str = "",
    message_ts: str = "",
    primary_thread_id: Optional[str] = None,
    person_ids: Optional[List[str]] = None,
    classification_confidence: Optional[float] = None,
    pending_review: bool = False,
    review_reason: str = "",
    urgency: Optional[str] = None,
    source_skill: str = "scan-for-commitments",
) -> dict:
    """One qualifying Slack message → one canonical `commitment` event dict,
    with the full capture block enforced in code (Stage-D kind, S2 due-nudge,
    Stage-E counterparty receipts, pending_review inversion). Construction
    only — append the batch through `event_gate.append_event` (ids are minted
    and seq stamped inside the writer lock; C4's semantic dedup fires there).

    Raises SlackItemError on anything the extraction must go back and do.
    """
    title = (title or "").strip()
    if not title:
        raise SlackItemError("a Slack commitment needs a non-empty title")
    source_ref = slack_source_ref(permalink)

    if direction not in (DIRECTION_USER_SENT, DIRECTION_NAMES_USER):
        raise SlackItemError(
            f"Slack capture {source_ref} has direction {direction!r} — only "
            f"the user's own messages ({DIRECTION_USER_SENT!r}) and messages "
            f"naming the user ({DIRECTION_NAMES_USER!r}) may become open "
            f"commitments; third-party items are out of scope for this leg "
            f"(W4c's observed tier will store them when it lands)"
        )

    due_str = (due or "").strip()
    data: dict = {
        "title": title,
        "kind": kind,
        "due": due_str,
        "source_ref": source_ref,
        # No parent event exists for a Slack message (unlike the meeting/email
        # legs) — the permalink IS the provenance; readers trace via source_ref.
    }
    if no_due:
        data["no_due"] = True
    if owner_id:
        data["owner_id"] = owner_id
    elif owner_external:
        data["owner_external"] = owner_external
    # MC1: normalize scalar + list counterparty inputs (single byte-identical).
    from commitment_parties import build_counterparty_fields
    data.update(build_counterparty_fields(
        counterparty_id=counterparty_id, counterparty_name=counterparty_name,
        counterparty_ids=counterparty_ids, counterparty_names=counterparty_names,
    ))
    if evidence:
        data["evidence"] = evidence[:200]
    if channel:
        data["channel"] = channel
    if urgency:
        data["urgency"] = urgency
    if pending_review:
        data["pending_review"] = True
        if review_reason:
            data["review_reason"] = review_reason

    # THE shared capture block (v4.6.1 W4c consolidation — one implementation
    # for every writer): Stage-D kind, S2 due-nudge (resolve relative phrases
    # against the MESSAGE's date, not the scan date), the promise-vs-task
    # rule, and the pending_review safety inversion (absence of the flag is
    # not consent; never unsets an extractor-set True).
    gate_commitment_data(
        data,
        subject=f"Slack commitment {source_ref}",
        classification_confidence=classification_confidence,
        error_cls=SlackItemError,
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
    if owner_id and owner_id not in pids:
        pids.append(owner_id)
    from commitment_parties import counterparty_ids as _cp_ids
    for _cid in _cp_ids(data):  # MC1: every resolved counterparty
        if _cid not in pids:
            pids.append(_cid)

    event: dict = {
        "type": "commitment",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "person_ids": pids,
        "data": data,
    }
    if classification_confidence is not None:
        event["classification_confidence"] = classification_confidence
    ts_iso = _slack_ts_to_iso(message_ts)
    if ts_iso:
        event["ts"] = ts_iso  # backdate to when the promise was made
    return event


def _title_key(title) -> str:
    return (str(title or "").strip().lower())[:_TITLE_DEDUP_CHARS]


def already_captured(workspace_root, permalink: str, title: str) -> bool:
    """Step 4 idempotency for the Slack leg, codified: True when a commitment
    with the same `slack:<permalink>` source_ref AND the same title (ci,
    first 60 chars — the scan's documented rule) is already on disk, OR the
    source_ref is already covered by a `commitment_resolved` /
    `thread_resolved` event. Shard-transparent via events_io. This keys on
    the scan's own (source_ref, title) rule only — C4's semantic cross-source
    layer runs separately inside the append path."""
    try:
        from events_io import iter_events
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(_HERE))
        from events_io import iter_events

    ref = slack_source_ref(permalink)
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


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "DEFAULT_MESSAGE_CAP",
    "DIRECTION_USER_SENT",
    "DIRECTION_NAMES_USER",
    "DIRECTION_OTHER",
    "NOISE_SUBTYPES",
    "SlackItemError",
    "slack_source_ref",
    "normalize_message",
    "classify_direction",
    "within_window",
    "cap_messages",
    "build_slack_commitment_event",
    "already_captured",
]
