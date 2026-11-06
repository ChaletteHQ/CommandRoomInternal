#!/usr/bin/env python3
"""
The inbound-mail Maintenance job (cr1#664, lane 2 of cr1#609): it OPENS
commitments from inbound mail and CLOSES them from inbound replies, on every
Maintenance fire.

WHY A JOB
---------
Inbound promises were captured only inside the Inbox chat (its Phase 4b), so
a missed Inbox run meant missed captures (cr1#514). The write half of the
inbound reconcile ran only on the commitments chat, which cr1#618 retired:
after that, nothing scheduled closed a commitment from an inbound reply. This
job is both halves on the Maintenance cadence, beside the sent and chat legs,
inside the already-authorized `maintenance` task (zero new registrations).

THE SHAPE (the chat leg's, `chat_reconcile.py`)
-----------------------------------------------
  plan     `plan_inbound_mail`, READ ONLY: the window (the one maintenance
           window, `catchup.maintenance_window`: since this job's own last
           receipt, 30 days on a first run), the fetch intent (`in_inbox` +
           `after`, compiled per provider by `connector_adapters/mail.py`;
           never `unread`: this job reads everything since its window), the
           accounts to read (every classified account with
           `write_to_business` on), the close mode, the primary user.
  fetch    the session, through the declared mail connector.
  open     `inbound_capture.capture_inbound_items`: the Inbox chat's own
           gate, cap and `below_bar` declaration, nothing relaxed.
  close    `reconcile_inbound_commitments.reconcile_inbound_and_receipt`,
           the write half, with `close_mode` as ruled on cr1#609
           (2026-11-03): a counterparty reply that only acknowledges
           PROPOSES; one that carries the deliverable (the sent matcher's
           evidence rules: attachment, thread id, send time) CLOSES. Then the
           roster-complete close (`apply_roster_complete_closes`) under this
           job's own name, in `close` mode only. Thresholds unchanged.
  receipt  ONE `pack_run` under `inbound-mail`, on every terminal state
           (skipped, blocked, complete). It lists every thread id plus
           message id the open half captured, which is what the Inbox chat's
           Phase 4b reads to yield (`captured_keys_since`).
  cursor   the receipt is the cursor: the next window starts at it, or at
           `window_incomplete_before` when this run left part of its window
           unhandled (a capped capture, a blocked read, a skip), so a gap is
           never stepped over.

ORDER. Captures before closes: the open half runs first, and the close half's
own fences (the message that created an item never closes it; a reply older
than the capture is not evidence) are the reconcile rail's, untouched.

PURE-ISH: no network I/O here. The session fetches through the connector and
hands dicts in; everything after that happens beside the data.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

JOB_ID = "inbound-mail"
SOURCE_SKILL = JOB_ID
RECEIPT_TYPE = "pack_run"

#: The window the Inbox chat's Phase 4b yields over (cr1#664 acceptance 2): a
#: thread id plus message id this job receipted in the last 24 hours is not
#: captured again by an Inbox fire.
YIELD_HOURS = 24

#: What one fetched message carries into the writer: the Inbox chat's own
#: per-thread shape (`inbox_helpers.run_inbox_surface`'s `items`), so the
#: capture judgement is the one Phase 4b already teaches. `captures` holds the
#: Phase 4b candidates; the writer anchors each to the message's own ids.
#: `labels`, `folder`, `folders` and `label` are every spelling `_labels`
#: reads, so a staged message keeps what the lists match on (cr1#770).
MESSAGE_FIELDS = ("message_id", "thread_id", "ts", "sender_email",
                  "sender_name", "sender_person_id", "subject", "body",
                  "has_attachment", "from_me", "labels", "folder", "folders",
                  "label", "captures")

#: What one capture candidate carries (orchestrator-inbox.md Phase 4b).
CAPTURE_FIELDS = ("direction", "title", "kind", "due", "no_due", "evidence",
                  "classification_confidence", "below_bar",
                  "below_bar_reason")

#: The message keys a capture inherits from its message when it names none.
_ANCHOR_KEYS = ("message_id", "thread_id", "ts", "sender_person_id",
                "sender_name")

NO_MAIL_REASON = "no mail connector is declared for this workspace"
NO_ACCOUNT_REASON = "no mail account is set to file into the business book"
ACCOUNTS_UNREADABLE_REASON = ("the workspace's mail accounts could not be "
                              "read, so no account can be shown to be in "
                              "business scope")


def _effective_fired_via(explicit):
    """`receipts.effective_fired_via`, behind an import that cannot break."""
    try:
        from receipts import effective_fired_via
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"
    try:
        return effective_fired_via(explicit)
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"


def _norm(v) -> str:
    return str(v or "").strip()


def _close_mode(value) -> str:
    """The close mode this run uses: the value asked for when it is one the
    ruling names, else `close`, the ruled default for inbound mail."""
    from reconcile_inbound_commitments import CLOSE_MODE_CLOSE, CLOSE_MODES

    return value if value in CLOSE_MODES else CLOSE_MODE_CLOSE


def connector_reads_for(workspace_root, env=None) -> Dict[str, Any]:
    """The declared mail connector's dials and lists, per purpose, read
    BEFORE the source (cr1#667, `connector_reads.for_job`, kind `mail`):
    `{"open": rec|None, "close": rec|None}`. Each record carries the
    connector's `dials` (its `close_mode` included), `include` / `exclude`
    labels or folders, and a plain `skip_reason` when that purpose is turned
    away (a dial off, or a desktop-only connector on a cloud fire). None per
    purpose when no mail connector is declared. Never raises."""
    out: Dict[str, Any] = {"open": None, "close": None}
    try:
        from connector_reads import for_job
    except Exception:  # noqa: BLE001
        return out
    for purpose in ("open", "close"):
        try:
            recs = for_job(workspace_root, "mail", purpose=purpose, env=env)
        except Exception:  # noqa: BLE001
            recs = []
        recs = [r for r in recs or [] if isinstance(r, dict)]
        pick = next((r for r in recs if r.get("category") == "email"),
                    recs[0] if recs else None)
        out[purpose] = pick
    return out


def _skip_of(rec) -> Optional[str]:
    return str(rec["skip_reason"]) if rec and rec.get("skip_reason") else None


def _dial_mode(reads: Dict[str, Any], explicit=None) -> str:
    """The close mode: the mail connector's `close_mode` dial when one is
    declared (the ruling: it decides, per connector), else the value asked
    for, else `close`, the ruled default (close on the deliverable, propose
    on an acknowledgement)."""
    rec = reads.get("close") or reads.get("open")
    dial = ((rec or {}).get("dials") or {}).get("close_mode")
    return _close_mode(dial if dial else explicit)


def _labels(msg) -> List[str]:
    """The labels or folders a fetched message names, if any (the sent
    leg's spellings)."""
    out: List[str] = []
    if not isinstance(msg, dict):
        return out
    for key in ("labels", "folder", "folders", "label"):
        v = msg.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v)
        elif isinstance(v, (list, tuple)):
            out.extend(x for x in v if isinstance(x, str) and x.strip())
    return out


def drop_excluded(read, items):
    """`(kept, n_excluded)`: a message with any label on the connector's
    exclude list, or with none of its labels on a non-empty include list, is
    never read by either half; one that names no label passes
    (`connector_reads.allowed`, the one place the lists are applied). All of
    a message's labels go to ONE call: it is the message that is kept or
    dropped, not each label (cr1#757)."""
    items = list(items or [])
    if not read or not (read.get("include") or read.get("exclude")):
        return items, 0
    try:
        from connector_reads import allowed
    except Exception:  # noqa: BLE001
        return items, 0
    kept = [m for m in items if allowed(read, *_labels(m))]
    return kept, len(items) - len(kept)


def _window(workspace_root, now=None) -> dict:
    """This job's maintenance window (cr1#663), computed before anything this
    run writes, so its basis is the previous run's receipt."""
    from catchup import maintenance_window

    local_now = None
    if isinstance(now, _dt.datetime):
        local_now = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    return maintenance_window(workspace_root, JOB_ID, now=local_now)


def fetch_accounts(workspace_root) -> List[Dict[str, Any]]:
    """The mail accounts this job reads: every classified account with
    `write_to_business` on, one fetch each, addressed by `acting_email`. An
    empty account map is the R4 back-compat case (everything in scope): ONE
    fetch, unaddressed. A populated map with no account in write scope is an
    empty list, and the writer skips with `NO_ACCOUNT_REASON`. An account map
    that cannot be read is None: FAIL CLOSED (review, cr1#664), because an
    unaddressed read would file a personal account's mail into the business
    book; the plan then says not ready and the job stays due. Never raises."""
    try:
        import connector_config as cc

        records = cc.accounts(workspace_root)
    except Exception:  # noqa: BLE001
        return None
    if not records:
        return [{"acting_email": None}]
    out: List[Dict[str, Any]] = []
    for rec in records:
        addr = _norm(rec.get("address"))
        if not addr:
            continue
        try:
            in_scope = cc.is_in_write_scope(addr, rec.get("account_id"),
                                            workspace_root)
        except Exception:  # noqa: BLE001
            in_scope = False
        if in_scope and {"acting_email": addr} not in out:
            out.append({"acting_email": addr})
    return out


def plan_inbound_mail(workspace_root, now=None) -> dict:
    """The inbound-mail job PLANNED beside the data - READ ONLY (on
    `workspace_access.RUN_HELPER_ALLOWLIST`).

    Answers what the fire's connector step needs and nothing it has to
    guess: the window, the provider-neutral fetch intent and its compiled
    query, the accounts to read, the host tool searches that load the mail
    connector, the message and capture fields, the close mode, the primary
    user and the fire start (the circularity fence). `provider` null is not
    a stop: the writer runs with no messages and lands the SKIP receipt.
    `ready` is false only when a mail backend is declared and no primary
    user resolves. Writes nothing."""
    from catchup import MAINTENANCE_WINDOW_FIELD

    now = now or _dt.datetime.now(_dt.timezone.utc)
    if isinstance(now, str):
        now = _dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.astimezone()
    # cr1#667: the dials and the lists, BEFORE any source is read.
    reads = connector_reads_for(workspace_root)
    window = _window(workspace_root, now=now)
    after = str(window.get("since_aware") or window.get("since") or "")[:10]
    intent = {"in_inbox": True, "after": after}
    try:
        from connector_adapters.provenance import resolve_mail_provider
        provider = resolve_mail_provider(workspace_root, None)
    except Exception:  # noqa: BLE001
        provider = None
    try:
        from connector_adapters.mail import compile_search
        query = compile_search(intent, provider) if provider else None
    except Exception:  # noqa: BLE001
        query = None
    accounts = fetch_accounts(workspace_root) if provider else []
    unreadable = accounts is None
    accounts = accounts or []
    try:
        from inbox_helpers import connector_discovery_queries
        queries = list(connector_discovery_queries(workspace_root)) if provider else []
    except Exception:  # noqa: BLE001
        queries = []
    try:
        from primary_user import resolve_primary_user
        user = resolve_primary_user(workspace_root)
    except Exception:  # noqa: BLE001
        user = None
    skip_reason = (NO_MAIL_REASON if not provider
                   else (None if unreadable
                         else (NO_ACCOUNT_REASON if not accounts else None)))
    open_skip, close_skip = _skip_of(reads["open"]), _skip_of(reads["close"])
    if not skip_reason and open_skip and close_skip:
        # Both halves turned away: fetch nothing, the writer lands the skip.
        skip_reason = "; ".join(dict.fromkeys((open_skip, close_skip)))
    read = reads["close"] or reads["open"]
    return {
        "job_id": JOB_ID,
        "provider": provider,
        "skip_reason": skip_reason,
        "after": after,
        MAINTENANCE_WINDOW_FIELD: window,
        "intent": intent,
        "query": query,
        "accounts": accounts,
        "connector_queries": [] if skip_reason else queries,
        "user_person_id": user,
        "message_fields": list(MESSAGE_FIELDS),
        "capture_fields": list(CAPTURE_FIELDS),
        "close_mode": _dial_mode(reads),
        "connector_read": read,
        "include": list((read or {}).get("include") or []),
        "exclude": list((read or {}).get("exclude") or []),
        "open_skip_reason": open_skip,
        "close_skip_reason": close_skip,
        "fire_start": now.astimezone(_dt.timezone.utc).isoformat(),
        "ready": (bool(user) or bool(skip_reason)) and not unreadable,
        "blocked_reason": (ACCOUNTS_UNREADABLE_REASON if unreadable
                           else (None if (user or skip_reason) else
                                 "the workspace's own person is not recorded")),
    }


def inbound_messages(items) -> List[Dict[str, Any]]:
    """The close half's list: every fetched message that is not the owner's
    own, in the reconcile rail's input shape."""
    out = []
    for item in items or []:
        if not isinstance(item, dict) or item.get("from_me"):
            continue
        out.append({"message_id": _norm(item.get("message_id")),
                    "ts": _norm(item.get("ts")),
                    "sender_person_id": _norm(item.get("sender_person_id")),
                    "sender_email": _norm(item.get("sender_email")),
                    "subject": _norm(item.get("subject")),
                    "body": _norm(item.get("body")),
                    "thread_id": _norm(item.get("thread_id")),
                    "has_attachment": bool(item.get("has_attachment"))})
    return out


def capture_items(items) -> List[Dict[str, Any]]:
    """The open half's candidates: each message's `captures`, anchored to the
    message's own ids and time unless the candidate names its own (the Inbox
    chat's `_capture_items` rule)."""
    out = []
    for item in items or []:
        if not isinstance(item, dict) or item.get("from_me"):
            continue
        for cand in item.get("captures") or []:
            if not isinstance(cand, dict):
                continue
            row = dict(cand)
            for key in _ANCHOR_KEYS:
                if not row.get(key) and item.get(key):
                    row[key] = item[key]
            out.append(row)
    return out


def _key(thread_id, message_id) -> Dict[str, str]:
    return {"thread_id": _norm(thread_id), "message_id": _norm(message_id)}


def _incomplete_marker(window: dict, oldest=None) -> Optional[str]:
    """`window_incomplete_before` for a run that left part of its window
    unhandled: the oldest unhandled point, clamped into the window, else the
    window's own start (catchup's one writer of the marker)."""
    from catchup import receipt_window_marker

    win = window if isinstance(window, dict) else {}
    return receipt_window_marker({"start": win.get("since"),
                                  "end": win.get("until")},
                                 incomplete=True, oldest_unhandled=oldest)


def _summary(r: dict) -> str:
    """One plain line, no field names, never claiming coverage it lacks."""
    if r.get("status") == "skipped":
        return ""
    if r.get("status") == "blocked":
        return ("New mail was not read this time, so nothing was opened or "
                "closed from it; the same mail is read on the next pass.")
    bits = []
    if r.get("n_captured"):
        n = r["n_captured"]
        bits.append(f"picked up {n} new item{'s' if n != 1 else ''}")
    if r.get("n_closed"):
        n = r["n_closed"]
        bits.append(f"closed {n} that came in")
    if r.get("n_proposed"):
        n = r["n_proposed"]
        bits.append(f"{n} to confirm")
    n_aside = r.get("n_excluded") or 0
    aside = (f" Your include or exclude list set aside {n_aside}."
             if n_aside else "")
    if not bits:
        n = r.get("n_fetched") or 0
        if n:
            return (f"Checked {n} new message{'s' if n != 1 else ''}; nothing "
                    "to pick up or close." + aside)
        if n_aside:  # cr1#770: mail was read, so never "no new mail"
            return (f"Read {n_aside} new message{'s' if n_aside != 1 else ''}, "
                    "none of them checked." + aside)
        return "No new mail to check."
    line = "From your mail: " + ", ".join(bits) + "." + aside
    if r.get("n_capped"):
        line += " The rest are picked up on the next pass."
    return line


def _log(workspace_root, receipt: dict, *, fired_via) -> None:
    """ONE `pack_run` under `inbound-mail`, through the canonical receipt
    writer, on every terminal state. A failure is stamped on the returned
    dict (the job then stays due), never swallowed silently."""
    from receipts import log_receipt

    extra = {k: v for k, v in receipt.items()
             if k not in ("status", "summary") and v is not None}
    try:
        log_receipt(workspace_root, JOB_ID, receipt_type=RECEIPT_TYPE,
                    status=receipt.get("status") or "complete",
                    fired_via=fired_via,
                    surfaced=int(receipt.get("n_closed") or 0),
                    extra_data=extra)
    except Exception as exc:  # noqa: BLE001
        print(f"inbound_mail_leg: receipt FAILED to land ({type(exc).__name__}: "
              f"{exc}) - this fire cannot be proven to have run",
              file=sys.stderr)
        receipt["receipt_failed"] = True
        receipt["receipt_error"] = f"{type(exc).__name__}: {exc}"


def run_inbound_mail(workspace_root, items, *, user_person_id=None,
                     provider=None, fired_via=None, fetch_blocked=None,
                     exclude_captured_since=None, close_mode=None,
                     accounts=None, source_skill: str = SOURCE_SKILL) -> dict:
    """Run the job end to end over the fetched `items` (one dict per message,
    `MESSAGE_FIELDS`) and land its one receipt. A WRITER.

    Three terminal states, never alike: SKIPPED (no mail backend declared, or
    no account in write scope: nothing read, the receipt says why, and a
    `connector_backend_changed` for `email` voids it for dueness); BLOCKED
    (a backend is declared but the read could not happen: nothing opened or
    closed); COMPLETE. The writer is named first, and an unresolved primary
    user raises before anything is read or written (Bug #102).

    THE DIALS (cr1#667) are read here too, beside the data, before anything
    is read: the mail connector's `open_commitments` dial gates the open
    half and `close_commitments` the close half, each skip recorded on the
    receipt in plain words; both off is a SKIPPED run. Its `close_mode` dial
    decides how a graded close lands (an explicit `close_mode` argument is
    used only when no mail connector is declared). A message outside the
    connector's include list, or on its exclude list, is read by neither
    half."""
    from catchup import MAINTENANCE_WINDOW_FIELD, WINDOW_INCOMPLETE_FIELD
    from receipts import require_writer_identity

    require_writer_identity(workspace_root=workspace_root)
    fired_via = _effective_fired_via(fired_via)
    window = _window(workspace_root)
    reads = connector_reads_for(workspace_root)
    mode = _dial_mode(reads, close_mode)
    open_skip, close_skip = _skip_of(reads["open"]), _skip_of(reads["close"])
    try:
        from connector_adapters.provenance import resolve_mail_provider
        provider = resolve_mail_provider(workspace_root, provider)
    except Exception:  # noqa: BLE001
        provider = provider or None
    base: Dict[str, Any] = {"leg": JOB_ID, "provider": provider,
                            "close_mode": mode, MAINTENANCE_WINDOW_FIELD: window,
                            "captured": [], "n_fetched": 0, "n_captured": 0,
                            "n_closed": 0, "n_proposed": 0,
                            "open_skip_reason": open_skip,
                            "close_skip_reason": close_skip}

    skip = None
    if not provider:
        skip = NO_MAIL_REASON
    elif accounts is not None and not list(accounts):
        skip = NO_ACCOUNT_REASON
    elif open_skip and close_skip:
        # cr1#667: both dials turned the connector away - read nothing.
        skip = "; ".join(dict.fromkeys((open_skip, close_skip)))
    if skip:
        receipt = dict(base, status="skipped", skip_reason=skip)
        receipt[WINDOW_INCOMPLETE_FIELD] = _incomplete_marker(window)
        receipt["summary"] = _summary(receipt)
        _log(workspace_root, receipt, fired_via=fired_via)
        return receipt

    if not user_person_id:
        from reconcile_sent_commitments import PrimaryUserUnresolvedError
        raise PrimaryUserUnresolvedError(
            "inbound-mail ABORTED: the primary user is unresolved. Direction "
            "is derived from owner against the user, so this run could only "
            "write a clean receipt claiming nothing to do. Nothing was read "
            "or written (Bug #102).")

    blocked = _norm(fetch_blocked)
    if blocked:
        receipt = dict(base, status="blocked", blocked_reason=blocked)
        receipt[WINDOW_INCOMPLETE_FIELD] = _incomplete_marker(window)
        receipt["summary"] = _summary(receipt)
        _log(workspace_root, receipt, fired_via=fired_via)
        return receipt

    items = [i for i in (items or []) if isinstance(i, dict)]
    items, n_excluded = drop_excluded(reads["close"] or reads["open"], items)
    messages = inbound_messages(items)
    candidates = capture_items(items) if not open_skip else []

    # OPEN - the Inbox chat's own gate, cap and below-bar declaration; not
    # run at all when the connector's `open_commitments` dial is off.
    from inbound_capture import capture_inbound_items

    opened = (capture_inbound_items(workspace_root, candidates,
                                    user_person_id=user_person_id,
                                    source_skill=source_skill,
                                    provider=provider)
              if not open_skip else {})
    thread_of = {}
    ts_of = {}
    for cand in candidates:
        mid = _norm(cand.get("message_id"))
        if mid:
            thread_of.setdefault(mid, cand.get("thread_id"))
            ts_of.setdefault(mid, _norm(cand.get("ts")))
    captured = []
    for row in list(opened.get("opened") or []) + list(opened.get("observed") or []):
        key = _key(thread_of.get(_norm(row.get("message_id"))), row.get("message_id"))
        if key["message_id"] and key not in captured:
            captured.append(key)

    # CLOSE - the write half of the inbound rail, in the ruled close mode.
    from reconcile_inbound_commitments import (CLOSE_MODE_CLOSE,
                                               reconcile_inbound_and_receipt)

    closed: Dict[str, Any] = {}
    if not close_skip:
        closed = reconcile_inbound_and_receipt(
            workspace_root, messages, user_person_id=user_person_id,
            source_skill=source_skill, fired_via=fired_via, provider=provider,
            exclude_captured_since=exclude_captured_since, close_mode=mode)
    roster = {"closed": [], "skipped": [], "errors": [], "batch_id": None}
    if mode == CLOSE_MODE_CLOSE and not close_skip:
        from reconcile_sent_commitments import apply_roster_complete_closes
        roster = apply_roster_complete_closes(
            workspace_root, source_skill=source_skill,
            closed_by=user_person_id)

    receipt = dict(base, status="complete")
    receipt.update({
        "captured": captured,
        "n_fetched": len(messages),
        "n_excluded": n_excluded,
        "n_candidates": int(opened.get("n_candidates") or 0),
        "n_captured": int(opened.get("n_captured") or 0),
        "n_deduped": int(opened.get("n_deduped") or 0),
        "n_below_bar": int(opened.get("n_below_bar") or 0),
        "n_capped": int(opened.get("n_capped") or 0),
        "n_capture_errors": int(opened.get("n_errors") or 0),
        "n_waiting_on": int(opened.get("n_waiting_on") or 0),
        "n_reply_owed": int(opened.get("n_reply_owed") or 0),
        "n_closed": int(closed.get("n_auto_closed") or 0)
                    + len(roster.get("closed") or []),
        "n_proposed": int(closed.get("n_pending") or 0),
        "n_close_mode_proposed": int(closed.get("n_close_mode_proposed") or 0),
        "n_updated": int(closed.get("n_updated") or 0),
        "n_partial_receipts": int(closed.get("n_partial_receipts") or 0),
        "n_roster_closed": len(roster.get("closed") or []),
        "close_batch_id": closed.get("batch_id"),
        "roster_batch_id": roster.get("batch_id") if roster.get("closed") else None,
    })
    if receipt["n_capped"]:
        # A capped capture leaves the deferred slice inside the window: the
        # next window starts at the oldest message it did not get to.
        # Compared as instants, never as strings: connectors spell one
        # time several ways (`Z`, `+00:00`, fractions).
        from cru_match import _parse_ts

        capped = []
        for c in opened.get("capped") or []:
            raw = ts_of.get(_norm(c.get("message_id")))
            when = _parse_ts(raw) if raw else None
            if when is not None:
                capped.append((when, raw))
        oldest = min(capped)[1] if capped else None
        receipt[WINDOW_INCOMPLETE_FIELD] = _incomplete_marker(window, oldest)
    receipt["summary"] = _summary(receipt)
    _log(workspace_root, receipt, fired_via=fired_via)
    receipt["close"] = {k: closed.get(k) for k in
                        ("resolved", "pending", "updated", "summary")}
    return receipt


def _receipts(workspace_root, *, since=None) -> list:
    from receipts import iter_receipts

    return [r for r in iter_receipts(workspace_root, task_ids=[JOB_ID],
                                     since=since)
            if r.get("type") == RECEIPT_TYPE]


def validate_inbound_mail_ran(workspace_root) -> dict:
    """Read the newest `inbound-mail` receipt back. SKIPPED is `ok` and not
    `ran` (a mail-less workspace must not read as a failing one); BLOCKED is
    not `ok` (a read that could not happen is not a read that found
    nothing); no receipt is not `ok`."""
    rows = _receipts(workspace_root)
    if not rows:
        return {"ok": False, "ran": False,
                "reason": "no inbound-mail receipt: the job did not run"}
    data = rows[-1]["raw"].get("data") or {}
    status = data.get("status")
    if status == "skipped":
        return {"ok": True, "ran": False, "status": status,
                "reason": data.get("skip_reason")}
    if status == "blocked":
        return {"ok": False, "ran": False, "status": status,
                "reason": "the mail read did not happen: "
                          + str(data.get("blocked_reason") or "recorded as blocked")}
    return {"ok": True, "ran": True, "status": status,
            "n_captured": data.get("n_captured"),
            "n_closed": data.get("n_closed"),
            "n_proposed": data.get("n_proposed")}


def captured_keys_since(workspace_root, *, now=None,
                        hours: float = YIELD_HOURS) -> set:
    """Every `(thread_id, message_id)` an `inbound-mail` receipt in the last
    `hours` lists as captured: what the Inbox chat's Phase 4b yields to.
    Never raises: an unreadable book yields nothing, which is the chat's own
    capture exactly as before."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if isinstance(now, str):
        now = _dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.astimezone()
    since = now - _dt.timedelta(hours=float(hours))
    out: set = set()
    try:
        rows = _receipts(workspace_root, since=since)
    except Exception:  # noqa: BLE001
        return out
    for r in rows:
        data = r["raw"].get("data") or {}
        for key in data.get("captured") or []:
            if isinstance(key, dict) and _norm(key.get("message_id")):
                out.add((_norm(key.get("thread_id")), _norm(key.get("message_id"))))
    return out


__all__ = [
    "JOB_ID", "SOURCE_SKILL", "RECEIPT_TYPE", "YIELD_HOURS",
    "MESSAGE_FIELDS", "CAPTURE_FIELDS", "NO_MAIL_REASON", "NO_ACCOUNT_REASON",
    "ACCOUNTS_UNREADABLE_REASON",
    "fetch_accounts", "connector_reads_for", "drop_excluded",
    "plan_inbound_mail", "inbound_messages",
    "capture_items", "run_inbound_mail", "validate_inbound_mail_ran",
    "captured_keys_since",
]
