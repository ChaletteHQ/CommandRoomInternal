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
  page     cr1#941: the fetch reads ONE page of the window
           (`catchup.paged_window`, shared with meeting capture, cr1#948),
           `after` to `before`, and the complete receipt moves the marker to
           where the page ended (`_page_fields`), so a first run's 30 days
           reach the present over a few fires. A read that stopped short is
           a partial read on a complete receipt, never an unreachable
           connector.

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
#: `account` (cr1#755) is the address the fetch was addressed to (the plan's
#: `acting_email`): on a seat that has classified its accounts, a message
#: with no account, or from an account that does not file into the business
#: book, is refused before either half reads it, and the receipt says so.
#: `attachment_names` (cr1#764) is what the connector calls each attached
#: file: the close rail's invite, image and file-name rules read it.
MESSAGE_FIELDS = ("message_id", "thread_id", "ts", "sender_email",
                  "sender_name", "sender_person_id", "subject", "body",
                  "has_attachment", "from_me", "labels", "folder", "folders",
                  "label", "captures", "account", "attachment_names")

#: The two reasons a staged row is refused by the account wall (cr1#755).
REFUSED_NO_ACCOUNT = "no_account"
REFUSED_OUT_OF_SCOPE = "out_of_scope"
REFUSED_UNREADABLE = "accounts_unreadable"

#: What one capture candidate carries (orchestrator-inbox.md Phase 4b).
CAPTURE_FIELDS = ("direction", "title", "kind", "due", "no_due", "evidence",
                  "classification_confidence", "below_bar",
                  "below_bar_reason")

#: The message keys a capture inherits from its message when it names none.
_ANCHOR_KEYS = ("message_id", "thread_id", "ts", "sender_person_id",
                "sender_name", "account")

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
    run writes, so its basis is the previous run's receipt.

    cr1#941: with the ONE page of it this fire reads (`catchup.paged_window`,
    the mechanism cr1#948 built for meeting capture), under `window_page`. A
    first run's 30 days (about 600 threads on a busy seat) are read a few
    days per fire, oldest first, instead of standing as one read a
    background fire stops partway through and opens again from the same
    start on every fire. A window shorter than a page is one page, read
    exactly as before. The page is then counted in whole UTC days
    (`_day_page`), because the inbox fetch can only be bounded by dates."""
    from catchup import paged_window

    local_now = None
    if isinstance(now, _dt.datetime):
        local_now = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    return _day_page(paged_window(workspace_root, JOB_ID, now=local_now))


def _day_page(window: dict) -> dict:
    """The page in whole UTC days (cr1#941 review F2). The inbox fetch takes
    dates (`fetch_floor`, `fetch_ceiling`), so a page of a quarter day still
    fetched two or three whole days: halving after a short read never shrank
    the query, and a seat whose few days exceeded one fire stopped short at
    the same start on every fire. Here the page ends on a UTC midnight, the
    page's day count (its span rounded, never under ONE day) after the start
    of the day it opens in, or at the window's end when that comes first. So
    the query is exactly the page's days, a halved page fetches fewer days,
    and the smallest page is one day. The page's start is untouched: it is
    the window's start, so nothing before it is stepped over."""
    from catchup import PAGE_FIELD, _parse_iso_local, to_connector_iso

    win = window if isinstance(window, dict) else {}
    page = win.get(PAGE_FIELD)
    since = _parse_iso_local((page or {}).get("since"))
    end = _parse_iso_local(win.get("until"))
    if not isinstance(page, dict) or since is None or end is None:
        return window
    try:
        span = float(page.get("span_days") or 1.0)
    except (TypeError, ValueError):
        span = 1.0
    n_days = max(1, int(span + 0.5))
    day0 = _dt.datetime.combine(since.astimezone(_dt.timezone.utc).date(),
                                _dt.time(), _dt.timezone.utc)
    stop = (day0 + _dt.timedelta(days=n_days)).astimezone().replace(tzinfo=None)
    until = min(end, stop)
    out = dict(win)
    out[PAGE_FIELD] = dict(
        page, until=until.isoformat(), until_aware=to_connector_iso(until),
        days=round(max(0.0, (until - since).total_seconds()) / 86400.0, 6),
        span_days=float(n_days), last=until >= end)
    return out


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


def fetch_floor(window) -> str:
    """The date the inbox fetch reads from (cr1#745): the window's start as
    an instant, dated in UTC (the zone every receipt and cursor is stored
    in), one day of overlap behind it on a cursor run (the sent leg's
    `CURSOR_OVERLAP_DAYS`, imported rather than typed, and the sent leg's
    own rule: a first run or a capped gap keeps the bare 30-day date, so
    the ruled bound is not widened). Cutting the start's machine-local text
    to ten characters gave two fires in two zones two different floors, and
    mail from the evening before the date could fall outside every fetch.
    Empty when the window has no readable start."""
    from catchup import WINDOW_REASON_CURSOR
    from reconcile_sent_commitments import CURSOR_OVERLAP_DAYS

    win = window if isinstance(window, dict) else {}
    raw = _norm(win.get("since_aware") or win.get("since"))
    if not raw:
        return ""
    try:
        since = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:10]
    if since.tzinfo is None:
        since = since.astimezone()
    floor = since.astimezone(_dt.timezone.utc)
    if (win.get("reason", WINDOW_REASON_CURSOR) == WINDOW_REASON_CURSOR
            and not win.get("window_incomplete_before")):
        # cr1#941: a window held at a carry-over marker opens exactly where
        # the last read stopped, so it takes no overlap day: with one, a page
        # halved after a short read would fetch a day more than the page.
        floor -= _dt.timedelta(days=CURSOR_OVERLAP_DAYS)
    return floor.date().isoformat()


def _page_until(window) -> Optional[str]:
    """The fetched page's end as an aware ISO instant, or None when the page
    reaches the present (the fetch had no upper bound)."""
    from catchup import PAGE_FIELD

    page = (window or {}).get(PAGE_FIELD) if isinstance(window, dict) else None
    if not isinstance(page, dict) or page.get("last") is not False:
        return None
    return page.get("until_aware") or None


def _as_fetched(window: dict, page_until) -> dict:
    """The writer's window with its page ended where the PLAN's fetch ended
    (cr1#941 review). The writer computes the window again at its own moment,
    and on a first run or a capped gap the window's start is "30 days ago":
    if its UTC date rolls between the plan and the write, `_day_page` would
    end the page a day later than the fetch did, and the marker would step
    over a day nobody read. So:
      `page_until` handed (the plan's) -> the page ends there, never later;
      none handed (a prompt that predates the field), on a window whose
        start moves with the clock (a first run or a capped gap) -> the page
        is ended as `_day_page` would have ended it with the start an hour
        earlier, so a UTC date that rolled while the fire ran never adds a
        day nobody fetched (a cursor window's start is fixed: unchanged);
      otherwise the page is as computed."""
    from catchup import (PAGE_FIELD, WINDOW_REASON_CURSOR, _parse_iso_local,
                         to_connector_iso)

    win = window if isinstance(window, dict) else {}
    page = win.get(PAGE_FIELD)
    if not isinstance(page, dict):
        return window
    since = _parse_iso_local(page.get("since"))
    until = _parse_iso_local(page.get("until"))
    end = _parse_iso_local(win.get("until"))
    if since is None or until is None or end is None:
        return window
    handed = _parse_iso_local(page_until)
    if handed is not None:
        new_until = min(max(handed, since), end)
    elif (page.get("last") is False
          and win.get("reason") != WINDOW_REASON_CURSOR):
        early = (since - _dt.timedelta(hours=1)).astimezone(_dt.timezone.utc)
        day0 = _dt.datetime.combine(early.date(), _dt.time(), _dt.timezone.utc)
        n_days = max(1, int(float(page.get("span_days") or 1.0) + 0.5))
        stop = (day0 + _dt.timedelta(days=n_days)).astimezone().replace(tzinfo=None)
        new_until = max(since, min(until, stop))
    else:
        return window
    out = dict(win)
    out[PAGE_FIELD] = dict(
        page, until=new_until.isoformat(),
        until_aware=to_connector_iso(new_until),
        days=round(max(0.0, (new_until - since).total_seconds()) / 86400.0, 6),
        last=new_until >= end)
    return out


def fetch_ceiling(window) -> str:
    """The date the inbox fetch reads up to, exclusive (cr1#941): the end of
    this fire's page, which `_day_page` puts on a UTC midnight, so the date
    is that day and the fetch reads exactly the page's days, no more. A page
    end that is not a midnight (a window `_day_page` could not shape) is
    rounded up to the next date, so the page's last day is still read whole.
    Empty when the page reaches the present: the read then has no upper
    bound, exactly as before paging.

    `fetch_floor` opens the fetch at the page's start, which is always the
    window's start; it takes its one overlap day only on a window that opens
    at the last receipt, never on one held at a carry-over marker."""
    from catchup import PAGE_FIELD

    win = window if isinstance(window, dict) else {}
    page = win.get(PAGE_FIELD)
    if not isinstance(page, dict) or page.get("last") is not False:
        return ""
    raw = _norm(page.get("until_aware") or page.get("until"))
    if not raw:
        return ""
    try:
        until = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if until.tzinfo is None:
        until = until.astimezone()
    end = until.astimezone(_dt.timezone.utc)
    if end.time() != _dt.time():
        end += _dt.timedelta(days=1)
    return end.date().isoformat()


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
    after = fetch_floor(window)
    # cr1#941: the fetch reads ONE page of the window, `after` to `before`;
    # no `before` on the page that reaches the present.
    before = fetch_ceiling(window)
    intent = {"in_inbox": True, "after": after}
    if before:
        intent["before"] = before
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
        "before": before or None,
        # cr1#941 review: the end of the page this plan fetched, handed back
        # to the writer so its marker lands where the fetch stopped; None on
        # the page that reaches the present.
        "page_until": _page_until(window),
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
    from reconcile_inbound_commitments import attachment_flag, attachment_names
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
                    # cr1#764: the text "false" reads as False.
                    "has_attachment": attachment_flag(item),
                    "attachment_names": attachment_names(item),
                    "account": _norm(item.get("account")).lower()})
    return out


def partition_by_account(workspace_root, items) -> tuple:
    """cr1#755: the account wall for THIS job, applied before either half
    reads a row. Returns `(kept, refused)` where `refused` is
    `{"n": int, "no_account": int, "out_of_scope": int}`.

    On a seat that has classified at least one mail account
    (`connector_config.account_map_populated`), every staged row must name
    the account it was read as (`account`), and that account must file into
    the business book (`is_in_write_scope`); a row that names none is
    refused, never read as business. On a seat that has classified nothing
    (R4: the wall is a no-op) every row is kept, today's behaviour."""
    refused = {"n": 0, REFUSED_NO_ACCOUNT: 0, REFUSED_OUT_OF_SCOPE: 0,
               REFUSED_UNREADABLE: 0}
    rows = [i for i in (items or []) if isinstance(i, dict)]
    try:
        import connector_config as cc
        populated = cc.account_map_populated(workspace_root)
    except Exception:  # noqa: BLE001 - fail CLOSED: no row can be shown in scope
        refused["n"] = refused[REFUSED_UNREADABLE] = len(rows)
        return [], refused
    if not populated:
        return rows, refused
    kept = []
    for item in rows:
        addr = _norm(item.get("account")).lower()
        if not addr:
            refused["n"] += 1
            refused[REFUSED_NO_ACCOUNT] += 1
            continue
        try:
            in_scope = cc.is_in_write_scope(address=addr, workspace_root=workspace_root)
        except Exception:  # noqa: BLE001 - cannot be shown in scope -> refused
            in_scope = False
        if not in_scope:
            refused["n"] += 1
            refused[REFUSED_OUT_OF_SCOPE] += 1
            continue
        kept.append(item)
    return kept, refused


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


def _page_fields(window: dict, read_complete, *, hold_start: bool) -> dict:
    """What a COMPLETE inbound-mail receipt carries about its page (cr1#941):
    `catchup.page_receipt_fields`, the writer half of cr1#948's pager, with
    no `oldest_unhandled`, ever. The inbox read is newest first, so a short
    read left the OLDER mail unlisted, and a message the run did handle says
    nothing about where the unlisted mail begins (cr1#948 review F1).

    The answer, merged into the receipt as it stands:
      read to the end of a page that is not the last -> the marker at the
        page's end, `read_partial` and the page's own line: the next fire
        reads the next page;
      read short (`read_complete` false) -> the marker at the page's START,
        the next page half the span read: the same mail is read again in a
        smaller piece, and nothing is skipped;
      the page read, but some of it not handled (`hold_start`: a capped
        capture; never a message whose time could not be read, which is
        named on the receipt and does not hold the page, review F1) -> the
        marker at the page's start, the next page the same span, so what was
        held is offered again (already-captured mail dedupes; the cap counts
        writes, so the page still moves);
      the last page read whole -> no marker: the window reached the present.

    A partial read is never a connector that could not be reached: nothing
    here names one."""
    from catchup import (PAGE_FIELD, WINDOW_INCOMPLETE_FIELD,
                         page_partial_line, page_receipt_fields)

    out = page_receipt_fields(window, read_complete=read_complete)
    page = dict(out.get(PAGE_FIELD) or {})
    if hold_start and page.get("read") != "short":
        marker = _incomplete_marker(window)
        if marker is not None:
            out[WINDOW_INCOMPLETE_FIELD] = marker
        page.update(read="capped", next_days=page.get("span_days")
                    or page.get("next_days"))
        out[PAGE_FIELD] = page
        out["read_partial"] = True
        out["partial_line"] = page_partial_line(page)
    return out


_NO_NEW_MAIL = "No new mail to check."


def _summary(r: dict) -> str:
    """One plain line, no field names, never claiming coverage it lacks."""
    if r.get("status") == "skipped":
        return ""
    if r.get("status") == "blocked":
        return ("New mail was not read this time, so nothing was opened or "
                "closed from it; the same mail is read on the next pass.")
    line = _read_summary(r)
    if r.get("n_bad_time") and not r.get("n_fetched") and line == _NO_NEW_MAIL:
        line = ""  # mail did arrive: the next sentence says what happened to it
    return (line + _bad_time_line(r) + _page_line(r)).strip()


def _page_line(r: dict) -> str:
    """cr1#941: the page's own words (`catchup.page_partial_line`), last: how
    far this pass read and where the next one carries on."""
    tail = str(r.get("partial_line") or "")
    return (" " + tail) if tail else ""


def _bad_time_line(r: dict) -> str:
    """cr1#767: the messages set aside for a time nothing could read."""
    n = r.get("n_bad_time") or 0
    if not n:
        return ""
    verb = "were" if n != 1 else "was"
    return (f" {n} message{'s' if n != 1 else ''} arrived with a time that "
            f"could not be read and {verb} set aside; this run's record "
            "names it, and the rest of the mail moves on.")


def _read_summary(r: dict) -> str:
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
    n_ref = r.get("n_refused") or 0
    if n_ref:
        # cr1#755: the account wall, in plain words and with its reasons.
        by = r.get("refused") or {}
        why = []
        if by.get(REFUSED_NO_ACCOUNT):
            why.append(f"{by[REFUSED_NO_ACCOUNT]} named no account")
        if by.get(REFUSED_OUT_OF_SCOPE):
            why.append(f"{by[REFUSED_OUT_OF_SCOPE]} came from an account that "
                       "does not file into your business book")
        if by.get(REFUSED_UNREADABLE):
            why.append(f"{by[REFUSED_UNREADABLE]} could not be matched to an "
                       "account because your mail accounts could not be read")
        aside += (f" {n_ref} message{'s were' if n_ref != 1 else ' was'} not "
                  f"read into the book: " + "; ".join(why) + ".")
    if not bits:
        n = r.get("n_fetched") or 0
        if n:
            return (f"Checked {n} new message{'s' if n != 1 else ''}; nothing "
                    "to pick up or close." + aside + _held_lines(r))
        if n_aside:  # cr1#770: mail was read, so never "no new mail"
            return (f"Read {n_aside} new message{'s' if n_aside != 1 else ''}, "
                    "none of them checked." + aside + _held_lines(r))
        if n_ref:  # cr1#755: mail was read and every row was refused
            return (f"Read {n_ref} new message{'s' if n_ref != 1 else ''}, "
                    "none of them checked." + aside + _held_lines(r))
        return _NO_NEW_MAIL + _held_lines(r)
    line = "From your mail: " + ", ".join(bits) + "." + aside
    if r.get("n_capped"):
        line += " The rest are picked up on the next pass."
    return line + _held_lines(r)


#: How many refused captures a receipt spells out; `n_capture_errors` counts all.
CAPTURE_ERRORS_KEPT = 20


def _held_lines(r: dict) -> str:
    """What this run says about its own edges, in plain words: captures it
    could not write and a read that did not reach the end of its window
    (cr1#742). Empty when neither happened."""
    out = ""
    n = r.get("n_capture_errors") or 0
    if n:
        # The reasons stay on the receipt, under `capture_errors`: a writer's
        # refusal is field names and error text, not an owner's line.
        out += f" {n} item{'s' if n != 1 else ''} could not be picked up."
    if r.get("read_partial") and not r.get("partial_line"):
        out += (" The mailbox had more than this pass read; the same span "
                "is read again on the next pass.")
    return out


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
                     accounts=None, source_skill: str = SOURCE_SKILL,
                     read_complete=None, page_until=None) -> dict:
    """Run the job end to end over the fetched `items` (one dict per message,
    `MESSAGE_FIELDS`) and land its one receipt. A WRITER.

    `page_until` (cr1#941): the plan's own `page_until`, where the fetch's
    page ended; the receipt's marker never lands past it (`_as_fetched`).

    `read_complete` (cr1#742): did the fetch reach the end of its window?
    False is a PARTIAL read (the connector had more pages than the run
    read): the receipt says so (`read_partial`), and it carries the window's
    start as `window_incomplete_before`, so the next window starts where
    this one did and the mail the run never saw is read then. True, or None
    from a caller that predates the flag, is a read of the whole window.

    A capture the job could not write (a due date that is not a date, an
    unknown kind, a mistyped direction, no due and no `no_due`) is named on
    the receipt under `capture_errors` with its reason. It is not offered
    again: cr1#771 stays open.

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
    # cr1#941 review: the page ends where the plan's fetch ended
    # (`page_until`, the plan's), never at a later page computed now.
    window = _as_fetched(_window(workspace_root), page_until)
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
    # cr1#755: the account wall FIRST. A row with no account, or from an
    # account that does not file into the business book, reaches neither
    # half; the receipt counts it and says why.
    items, refused = partition_by_account(workspace_root, items)
    items, n_excluded = drop_excluded(reads["close"] or reads["open"], items)
    # cr1#767: one spelling of a message's time before either half reads
    # it. A header date or an epoch number becomes the house ISO form; a
    # time nothing can read refuses the message, with a count and the raw
    # text on the receipt, and holds the window (below): a commitment
    # stamped with that text could never be closed by a reply.
    from message_time import normalize_messages
    from trusted_now import trusted_now_utc
    clock = trusted_now_utc(workspace_root)
    items, bad_times = normalize_messages(items, now=clock)
    messages = inbound_messages(items)
    candidates = capture_items(items) if not open_skip else []
    # A candidate may name its own time; it is read the same way.
    candidates, bad_captures = normalize_messages(candidates, now=clock)
    bad_times = bad_times + bad_captures

    # OPEN - the Inbox chat's own gate, cap and below-bar declaration; not
    # run at all when the connector's `open_commitments` dial is off.
    from inbound_capture import capture_inbound_items

    opened = (capture_inbound_items(workspace_root, candidates,
                                    user_person_id=user_person_id,
                                    source_skill=source_skill,
                                    provider=provider)
              if not open_skip else {})
    thread_of = {}
    for cand in candidates:
        mid = _norm(cand.get("message_id"))
        if mid:
            thread_of.setdefault(mid, cand.get("thread_id"))
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
        "n_refused": int(refused.get("n") or 0),
        "refused": {k: int(v) for k, v in refused.items() if k != "n"},
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
        "n_bad_time": len(bad_times),
    })
    if bad_times:
        receipt["bad_time_messages"] = bad_times
    # The messages this run leaves for the next one: a capped capture,
    # deferred. cr1#941: the page is read again from its start, never from
    # the oldest held message (`_page_fields`).
    held = list(opened.get("capped") or [])
    if receipt["n_capture_errors"]:
        # The count is `n_capture_errors`; a bad batch must not bloat the book.
        receipt["capture_errors"] = [
            {"message_id": _norm(e.get("message_id")),
             "title": _norm(e.get("title")), "reason": _norm(e.get("error"))}
            for e in opened.get("errors") or [] if isinstance(e, dict)
        ][:CAPTURE_ERRORS_KEPT]
    from catchup import read_reached_end
    read_complete = read_reached_end(read_complete)
    if read_complete is not None:
        receipt["read_complete"] = read_complete
    # cr1#941 review F1: a message whose time could not be read does NOT
    # hold the page. It is counted and named on this receipt
    # (`bad_time_messages`); holding for it pinned the page, since the same
    # message came back on every re-read and the window never moved.
    receipt.update(_page_fields(window, read_complete,
                                hold_start=bool(held or receipt["n_capped"])))
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
