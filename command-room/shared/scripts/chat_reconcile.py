#!/usr/bin/env python3
"""
The chat CLOSURE leg (SPEC CHATSCAN1 §B) — commitments discharged in chat.

WHAT THIS IS FOR
----------------
A promise made in a meeting and delivered over chat is invisible to every
closer we have. The mail rail closes on sent mail; the inbound rail closes on
replies; chat closes on nothing, so "just sent it" in a DM leaves the item open
forever and the next morning's brief tells the CEO to redo work they already
did. That is the reconcile-sent defect class, third channel.

WHY IT RIDES THE MAINTENANCE CADENCE AND NOT AN ON-DEMAND COMMAND
-----------------------------------------------------------------
M's ruling (2026-08-06): closing and review from chat are wired into the
maintenance cadence exactly like mail — a first-class registered job, not an
extra somebody has to remember to ask for. So `reconcile-chat` sits in
`maintenance_dispatcher.MAINTENANCE_JOBS` immediately after `reconcile-sent`,
with the same nominal cron and the same receipt discipline, and a maintenance
run that reconciles mail and not chat is VISIBLY incomplete rather than quietly
partial (`maintenance_dispatcher.roster_gap`). ZERO new scheduled tasks: a job
inside the already-authorized `maintenance` taskId needs no registration on any
client machine, which is the whole reason MAINT1 exists.

WHY THERE IS NO MATCHING LOGIC IN THIS FILE
-------------------------------------------
Scoring lives in the SENTMATCH and REPLYCLOSE seams
(`reconcile_sent_commitments.reconcile_sent` for the user's own outbound
messages, `reconcile_inbound_commitments.reconcile_inbound` for a
counterparty's). This module ADAPTS chat into the shape those matchers already
take and adapts their answers back — it does not re-derive a single threshold.
Forking the scoring would have meant three copies of the confidence bands, and
the WATCHGATE strength classes would have drifted the moment one copy was
tuned. Chat evidence is terse, so it will more often land in the propose-close
band than mail does; that is the CORRECT outcome of unchanged thresholds, not a
reason to move them.

NO POINTER, NO CLOSE
--------------------
Every close this leg writes carries a complete pointer back to the message that
justified it — `data.source_ref` (the canonical string every existing reader
and the dedup index already understand) AND `data.chat_source_ref` (the
structured `{provider, kind, chat_or_channel_id, message_id, ts}` the spec
requires, carrying provider and kind explicitly so a reader knows which of the
two id shapes it is holding). A candidate whose pointer cannot be built is
REFUSED and counted, never closed: the unauditable-close hole must not board a
third channel.

COVERAGE HONESTY
----------------
On a backend that cannot filter chat by date the connector silently degrades to
a partial per-conversation scan (`connector_adapters/chat.plan_scan`). This
leg records the scan mode and the coverage note on its receipt, and its summary
says so in plain language. A surface that claims a full reconcile it did not
run is worse than one that admits the gap.

PURE-ISH: this module does no network I/O. The SKILL fetches messages through
the declared chat backend's seam-resolved tools and hands dicts in; everything
after that — matching, closing, proposing, the receipt, the cursor — happens
here, atomically, as a side effect of actually running.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from connector_adapters import chat as _chat  # noqa: E402

JOB_ID = "reconcile-chat"
RECEIPT_EVENT_TYPE = "chat_reconcile"
CURSOR_KEY = "chat_reconcile_cursor"
SOURCE_SKILL = "reconcile-sent"

# First-declaration backfill horizon (§3: "a short fixed window, builder picks,
# receipted — never full history"). Seven days matches the Slack capture leg's
# DEFAULT_WINDOW_DAYS, so the two chat readers agree about how far back "recent
# chat" reaches; a workspace that wants more asks for it explicitly.
DEFAULT_BACKFILL_DAYS = 7

# Volume bounds. `MESSAGE_CAP` is the fetch bound the caller is told to honor;
# `CANDIDATE_CAP` is the bound on what may reach the matcher in one fire, which
# is the anti-bloat number: 200 messages of banter must not become 200 scoring
# candidates. Both report their overflow — a silent cap reads as full coverage.
MESSAGE_CAP = 400
CANDIDATE_CAP = 40

# Direction lanes, reusing the Slack capture leg's vocabulary verbatim rather
# than minting a second one (SLACK1 landed it; this consumes it).
try:
    from slack_capture import (DIRECTION_NAMES_USER, DIRECTION_OTHER,
                               DIRECTION_USER_SENT)
except Exception:  # pragma: no cover — defensive only
    DIRECTION_USER_SENT = "user_sent"
    DIRECTION_NAMES_USER = "names_user"
    DIRECTION_OTHER = "other"


class ChatReconcileError(RuntimeError):
    """The chat leg could not run in a way that would leave an honest record."""


def _norm(v) -> str:
    return str(v or "").strip()


def _short_date(ts) -> str:
    if not isinstance(ts, str) or not ts:
        return ""
    return ts[:10]


def _empty_counts() -> Dict[str, int]:
    """The counters that make this leg's zeros READABLE.

    `n_scanned` is the denominator. `n_candidates` says how many messages
    survived the relevance bound — the gap between the two IS the anti-bloat
    result, and a fire that reports 200 scanned / 200 candidates is a fire
    whose bound did not run. `n_refused_no_pointer` counts closes this leg
    REFUSED for want of an auditable pointer; a non-zero value is the fence
    working, not an error.
    """
    return {
        "n_scanned": 0,
        "n_dropped_noise": 0,
        "n_dropped_bad_ts": 0,
        "n_dropped_direction": 0,
        "n_candidates": 0,
        "n_candidates_over_cap": 0,
        "n_closed": 0,
        "n_proposed": 0,
        "n_refused_no_pointer": 0,
    }


# ---------------------------------------------------------------------------
# Normalization — one shape from two connectors
# ---------------------------------------------------------------------------

def normalize_chat_message(msg, *, provider: Optional[str],
                           user_chat_ids=(), user_names=(),
                           diagnostics: Optional[dict] = None) -> Optional[dict]:
    """One raw connector message → the leg's internal shape, or None when the
    message is noise this leg must never score.

    Accepts BOTH id shapes, because that is the difference between the two
    backends and it is not the caller's job to reconcile it:
      - `chat_or_channel_id` / `channel` / `channel_id` / `chat_id`
      - `message_id` / `ts` / `id`
    A message missing either half is dropped — a message the leg cannot point
    at is a message it must not close anything with, and the drop is counted
    rather than silent.

    `is_user` is resolved from the caller's own id list, never guessed from a
    display name alone: closing a commitment on someone else's message because
    two people share a first name is the failure this lane cannot afford.

    `diagnostics` (the EVORDER precedent) is incremented per drop REASON, so
    the receipt can tell "the connector sent us junk" apart from "the window
    was quiet". A drop nobody counts is how a malformed feed reads as an
    empty one.
    """
    diag = diagnostics if isinstance(diagnostics, dict) else {}

    def _drop(reason: str):
        diag[reason] = diag.get(reason, 0) + 1
        return None
    if not isinstance(msg, dict):
        return _drop("noise")
    text = _norm(msg.get("text") or msg.get("body") or msg.get("content"))
    if not text:
        return _drop("noise")
    if msg.get("bot_id") or msg.get("is_bot"):
        return _drop("noise")

    room = _norm(msg.get("chat_or_channel_id") or msg.get("channel_id")
                 or msg.get("channel") or msg.get("chat_id"))
    mid = _norm(msg.get("message_id") or msg.get("id") or msg.get("ts"))
    if not (room and mid):
        return _drop("noise")

    kind = _norm(msg.get("kind")).lower()
    if kind not in _chat.VALID_REF_KINDS:
        # The connector did not say. Ask the SEAM — this leg never decides an
        # id-shape question by looking at a product name; that is precisely
        # the branch the whole connector-agnostic posture exists to prevent.
        kind = _chat.default_ref_kind(
            provider, has_thread=bool(_norm(msg.get("thread_id"))),
            is_dm=bool(msg.get("is_dm") or msg.get("is_direct")))

    ts = _norm(msg.get("iso_ts") or msg.get("timestamp") or msg.get("sent_at"))
    if not ts:
        # Some backends' message id IS a timestamp. Whether this one's is, is
        # the seam's question, not the leg's — and a backend whose id is
        # opaque yields "", which DROPS the message rather than inventing a
        # time the stale-evidence fence would then trust.
        ts = _chat.iso_from_native_id(provider, mid)
    canonical_ts = _chat.canonical_iso(ts)
    if canonical_ts is None:
        # QUARANTINE, not tolerate. A message whose timestamp is absent OR
        # unparseable is dropped here and counted as `n_dropped_bad_ts`, so it
        # can neither be scored (it cannot be ordered against a promise) nor
        # reach the cursor. Before this, an unparseable string flowed straight
        # into `chat_reconcile_cursor` and, because the advance test is a raw
        # string comparison, every later ISO timestamp sorted below it — one
        # malformed message wedged the leg permanently. Dropping it lets the
        # fire advance past it on the very same run.
        return _drop("bad_ts")
    # NORMALIZE, don't just validate (review FR-1). Everything downstream —
    # the `max(ts)` ceiling, the oldest-first drain, the cursor comparison and
    # the pointer — reads THIS field, so it is re-spelled once, here, into the
    # single canonical form. A connector emitting basic-format ISO
    # (`20260708T213523`) is parseable and therefore survived the quarantine
    # above, and then sorted ABOVE every extended-format timestamp — the F-2
    # wedge, one layer down.
    ts = canonical_ts

    author = _norm(msg.get("author_id") or msg.get("user") or msg.get("from_id"))
    ids = {i for i in (user_chat_ids or ()) if i}
    is_user = bool(author and author in ids)
    if not is_user and msg.get("is_user") is True:
        is_user = True

    direction = DIRECTION_USER_SENT if is_user else DIRECTION_OTHER
    if not is_user:
        lowered = text.lower()
        for uid in ids:
            if f"<@{uid}>" in text:
                direction = DIRECTION_NAMES_USER
                break
        else:
            for name in user_names or ():
                token = _norm(name).lower()
                if len(token) >= 3 and token in lowered:
                    direction = DIRECTION_NAMES_USER
                    break

    return {
        "provider": (provider or "").lower(),
        "kind": kind,
        "chat_or_channel_id": room,
        "message_id": mid,
        "ts": ts,
        "text": text,
        "author_id": author,
        "is_user": is_user,
        "direction": direction,
        "thread_id": _norm(msg.get("thread_id")),
        "permalink": _norm(msg.get("permalink")),
        "has_attachment": bool(msg.get("has_attachment") or msg.get("files")),
        "counterparty_person_ids": [p for p in (msg.get("counterparty_person_ids")
                                                or msg.get("person_ids") or []) if p],
        "counterparty_names": [n for n in (msg.get("counterparty_names") or []) if n],
    }


def build_ref(msg: dict) -> dict:
    """The structured pointer for a normalized message. Raises
    `chat.ChatPointerError` when it cannot be completed."""
    return _chat.build_chat_source_ref(
        provider=msg.get("provider"),
        kind=msg.get("kind"),
        chat_or_channel_id=msg.get("chat_or_channel_id"),
        message_id=msg.get("message_id"),
        ts=msg.get("ts"),
        permalink=msg.get("permalink") or None,
    )


# ---------------------------------------------------------------------------
# Candidate selection — the anti-bloat bound
# ---------------------------------------------------------------------------

def select_candidates(messages: List[dict], *, cap: int = CANDIDATE_CAP):
    """`(candidates, counts, deferred)` — the messages that may reach the
    matcher, and the ones this fire is handing to the next one.

    The bound is RELEVANCE first, volume second:
      1. Third-party↔third-party chatter is dropped outright. It can neither
         close the user's promise nor discharge one owed to them, and it is
         the overwhelming majority of any channel. (Same lane split
         `slack_capture.classify_direction` already established — consumed,
         not re-derived.) These are DECIDED, not deferred: they were seen,
         classified, and correctly excluded, so they never hold the cursor.
      2. Whatever survives drains OLDEST FIRST, and the overflow — the NEWER
         remainder — is returned so the caller can hold the cursor behind it.

    Oldest-first is the correction the review's A5 fixture exposed. Capping
    newest-first came from `slack_capture.cap_messages`, which serves a
    RECENCY scan; this is a cursor-driven catch-up, and there the two orders
    are not interchangeable. Newest-first meant the overflow was always the
    OLDEST messages while the cursor advanced to the newest — so the 80
    dropped messages sat permanently behind a cursor that had already passed
    them, while the summary promised they would "get picked up next time".
    Draining oldest-first makes the deferred set the NEWER remainder, which
    the next fire reaches by simply not advancing the cursor past it: the
    backlog drains in order, the window still moves forward every run, and
    the promise on the receipt becomes true.
    """
    counts = _empty_counts()
    kept: List[dict] = []
    for m in messages or []:
        if not isinstance(m, dict):
            counts["n_dropped_noise"] += 1
            continue
        counts["n_scanned"] += 1
        if m.get("direction") == DIRECTION_OTHER:
            counts["n_dropped_direction"] += 1
            continue
        kept.append(m)
    kept.sort(key=lambda m: m.get("ts") or "")
    deferred: List[dict] = []
    if cap and cap > 0 and len(kept) > cap:
        deferred = kept[cap:]
        kept = kept[:cap]
        counts["n_candidates_over_cap"] = len(deferred)
    counts["n_candidates"] = len(kept)
    return kept, counts, deferred


# ---------------------------------------------------------------------------
# Scoring — the SENTMATCH / REPLYCLOSE seams, adapted
# ---------------------------------------------------------------------------

def _to_matcher_shape(msg: dict) -> dict:
    """One normalized chat message → the message dict the mail matchers take.

    `message_id` is the NATIVE half of the chat key (`<room>:<message id>`),
    never the whole key: the matchers hand it to
    `provenance.primary_artifact_key(provider, native_id)`, which adds the
    provider prefix itself. Passing a prefixed value would build
    `slack:slack:<…>`, match no commitment on disk, and turn the self-closure
    guard off without failing anything — the exact silent identity break the
    mail seam already paid for once.
    """
    ref = build_ref(msg)
    native = _chat.native_ref_id(ref)
    thread_native = None
    if msg.get("thread_id"):
        thread_native = (f"{_norm(msg['chat_or_channel_id']).lower()}:"
                         f"{_chat.normalize_message_id(msg.get('provider'), msg['thread_id']).lower()}")
    return {
        "message_id": native,
        "ts": msg.get("ts"),
        "thread_id": thread_native,
        "has_attachment": bool(msg.get("has_attachment")),
        "recipient_person_ids": list(msg.get("counterparty_person_ids") or []),
        "recipient_names": list(msg.get("counterparty_names") or []),
        # A chat message has no subject line. Leaving it None (rather than
        # echoing the body into it) keeps the title-overlap band scoring the
        # text once instead of twice — double-counting the same words would
        # inflate every chat match past a threshold nobody agreed to move.
        "subject": None,
        "body": msg.get("text"),
    }


def score_candidates(open_commitments, candidates: List[dict], *,
                     user_person_id: str, provider: Optional[str],
                     exclude_captured_since=None,
                     workspace_root=None) -> Dict[str, Any]:
    """Run the candidates through the EXISTING closure seams.

    Outbound (the user's own messages) goes to SENTMATCH; inbound (a
    counterparty's message naming the user) goes to REPLYCLOSE. Both return
    the same proposal shape, and neither is re-implemented here.

    Returns `{"auto_close": [...], "pending": [...], "by_native_id": {...}}`
    where `by_native_id` maps a proposal's `message_id` back to the chat
    message it came from, so the writer can rebuild the pointer.
    """
    from reconcile_inbound_commitments import reconcile_inbound
    from reconcile_sent_commitments import reconcile_sent

    outbound = [m for m in candidates if m.get("direction") == DIRECTION_USER_SENT]
    inbound = [m for m in candidates if m.get("direction") == DIRECTION_NAMES_USER]

    by_native: Dict[str, dict] = {}
    for m in candidates:
        native = _chat.native_ref_id(build_ref(m))
        if native:
            by_native[native] = m

    auto: List[dict] = []
    pending: List[dict] = []

    if outbound:
        res = reconcile_sent(
            open_commitments, [_to_matcher_shape(m) for m in outbound],
            user_person_id=user_person_id, provider=provider,
            exclude_captured_since=exclude_captured_since,
            workspace_root=workspace_root,
        )
        auto.extend(res.get("auto_close") or [])
        pending.extend(res.get("pending") or [])
    if inbound:
        res = reconcile_inbound(
            open_commitments, [_to_matcher_shape(m) for m in inbound],
            user_person_id=user_person_id, provider=provider,
            exclude_captured_since=exclude_captured_since,
            workspace_root=workspace_root,
        )
        auto.extend(res.get("auto_close") or [])
        pending.extend(res.get("pending") or [])

    # One row per commitment across both lanes — a close beats a confirm, and
    # within a tier the higher score wins. Without this a promise discharged
    # in a DM and acknowledged in a channel would be adjudicated twice.
    best: Dict[str, dict] = {}
    for row in auto:
        row = dict(row)
        row["recommendation"] = "auto_resolve"
        cid = str(row.get("commitment_id") or "")
        prev = best.get(cid)
        if cid and (prev is None or (row.get("score") or 0) > (prev.get("score") or 0)):
            best[cid] = row
    for row in pending:
        row = dict(row)
        row["recommendation"] = "pending_review"
        cid = str(row.get("commitment_id") or "")
        if not cid:
            continue
        prev = best.get(cid)
        if prev is None:
            best[cid] = row
        elif prev.get("recommendation") == "pending_review" and \
                (row.get("score") or 0) > (prev.get("score") or 0):
            best[cid] = row

    return {
        "auto_close": [r for r in best.values() if r["recommendation"] == "auto_resolve"],
        "pending": [r for r in best.values() if r["recommendation"] == "pending_review"],
        "by_native_id": by_native,
    }


# ---------------------------------------------------------------------------
# The cursor
# ---------------------------------------------------------------------------

def _entities_path(workspace_root) -> Path:
    try:
        from data_root import resolve as _resolve
        return _resolve(workspace_root) / "entities.json"
    except Exception:
        return Path(workspace_root) / "_hq" / "data" / "entities.json"


def read_cursor(workspace_root):
    """`(cursor_or_None, raw_doc)` for `workspace.chat_reconcile_cursor`.

    Its OWN cursor, deliberately not the mail one: the two legs read different
    connectors over different windows, and sharing a cursor would have a wide
    mail catch-up silently skip a week of chat (or the reverse)."""
    import json
    p = _entities_path(workspace_root)
    raw = json.loads(p.read_text(encoding="utf-8"))
    inner = raw["entities"] if isinstance(raw.get("entities"), dict) else raw
    ws = inner.get("workspace") if isinstance(inner.get("workspace"), dict) else {}
    stored = ws.get(CURSOR_KEY)
    # NORMALIZE LEGACY ON READ (review FR-1). Everything this leg writes from
    # here on is canonically spelled, but a cursor persisted by a pre-FR-1 fire
    # may carry any spelling `is_iso` used to accept — and comparing a stored
    # `20260708T213523` against a canonical `2026-07-08T21:35:23+00:00` is the
    # wedge again, from the other side. Re-spelling on read makes the
    # comparison canonical-vs-canonical without rewriting history: the stored
    # document is untouched, and an unparseable legacy value is passed through
    # verbatim rather than fabricated into a time it never was.
    canonical = _chat.canonical_iso(stored) if stored else None
    return (canonical if canonical is not None else stored), raw


def _write_cursor(workspace_root, new_cursor, *, source_skill: str) -> None:
    """Persist the chat cursor through the locked JSON writer, re-reading the
    doc first (the same reason `reconcile_sent_commitments._write_cursor`
    does: another phase of this fire may have written people into the file,
    and a snapshot taken before that would clobber them).

    REFUSES a non-ISO value. The quarantine in `normalize_chat_message` should
    already make this unreachable, but the cursor is the one piece of state a
    bad value corrupts PERMANENTLY — a non-ISO string sorts above every real
    timestamp under the raw string comparison the advance test uses, so the
    leg never advances again — and a fence guarding permanent corruption is
    worth having twice. Raises rather than skipping: silently declining to
    write would leave the caller reporting a cursor advance that never
    happened."""
    canonical = _chat.canonical_iso(new_cursor)
    if canonical is None:
        raise ChatReconcileError(
            f"refusing to persist chat cursor {new_cursor!r} — it is not an "
            "ISO-8601 instant, and a cursor that cannot be compared as a time "
            "wedges this leg permanently"
        )
    # And it is persisted in ONE spelling (review FR-1) — the last fence on
    # the one piece of state a bad value corrupts permanently.
    new_cursor = canonical
    from atomic_write import atomic_write_json_locked
    _cur, raw = read_cursor(workspace_root)
    inner = raw["entities"] if isinstance(raw.get("entities"), dict) else raw
    ws = inner.setdefault("workspace", {})
    if not isinstance(ws, dict):
        ws = {}
        inner["workspace"] = ws
    ws[CURSOR_KEY] = new_cursor
    atomic_write_json_locked(_entities_path(workspace_root), raw, holder=source_skill)


def backfill_floor(workspace_root, *, now=None, days: int = DEFAULT_BACKFILL_DAYS):
    """The ISO instant a first-ever chat fire should read back to.

    `(floor_iso, is_first_run)`. A first declaration reads a SHORT fixed
    window and says so on its receipt — never full history, which is the
    passive-ingestion failure mode this build is explicitly not."""
    cursor, _raw = read_cursor(workspace_root)
    if cursor:
        return cursor, False
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return (now - _dt.timedelta(days=days)).isoformat(), True


# ---------------------------------------------------------------------------
# The orchestrator + the receipt
# ---------------------------------------------------------------------------

def reconcile_chat_and_receipt(
    workspace_root,
    chat_messages,
    *,
    user_person_id,
    provider=None,
    user_chat_ids=(),
    user_names=(),
    scan_plan=None,
    source_skill: str = SOURCE_SKILL,
    fired_via: str = "scheduled",
    fetch_blocked=None,
    exclude_captured_since=None,
) -> dict:
    """Run the chat closure leg end-to-end and return a receipt whose fields
    exist only because the work ran.

    Four terminal states, and they must never look alike:
      - NO BACKEND. The workspace declares no chat backend. The leg is
        skipped SILENTLY (nothing is said to the user) and RECEIPTED
        (`status: "skipped"`), because a silent skip with no record is
        indistinguishable from a sweep that found nothing.
      - BLOCKED. A backend is declared but the read could not happen. Same
        posture as the mail leg's blocked run: the audit says so, the cursor
        does NOT advance over a window nobody read, and the validator refuses
        it.
      - DEGRADED. The read happened on the partial per-conversation path. It
        counts as a real run, and the receipt carries the scan mode + the
        coverage note so no surface claims a full reconcile.
      - COMPLETE.

    Chat evidence is terse, so expect the propose-close band to carry more of
    the traffic than it does on mail. That is the thresholds working, and
    nothing here moves them.
    """
    provider = _chat.resolve_chat_provider(workspace_root, provider)
    if not provider:
        receipt = _chat.skip_receipt(
            "no chat backend is declared for this workspace", leg=JOB_ID)
        receipt.update(_empty_counts())
        receipt["summary"] = ""      # deliberately nothing to say to the user
        receipt["closed"] = []
        receipt["proposed"] = []
        receipt["cursor_before"] = None
        receipt["cursor_after"] = None
        receipt["cursor_advanced"] = False
        _log_receipt(workspace_root, receipt, fired_via=fired_via,
                     source_skill=source_skill)
        return receipt

    if not user_person_id:
        raise ChatReconcileError(
            "reconcile-chat ABORTED: the primary user is unresolved. Every "
            "owner gate would match nothing and this run would write a clean "
            "audit claiming zero to close. Nothing was read or written and "
            "the cursor did not move (the Bug #102 posture, mirrored)."
        )

    cursor_before, _raw = read_cursor(workspace_root)
    plan = scan_plan or _chat.plan_scan(provider, date_filtered=True)

    blocked = _norm(fetch_blocked)
    if blocked:
        receipt = {
            "ran": False, "status": "blocked", "leg": JOB_ID,
            "provider": provider, "blocked_reason": blocked,
            "scan_mode": plan.get("mode"), "degraded": bool(plan.get("degraded")),
            "coverage_note": plan.get("coverage_note"),
            "cursor_before": cursor_before, "cursor_after": cursor_before,
            "cursor_advanced": False,
            "closed": [], "proposed": [],
            "summary": (f"Chat reconciliation did not run: {blocked}. Nothing "
                        "was read, so nothing was closed, and the window is "
                        "picked up whole on the next pass."),
        }
        receipt.update(_empty_counts())
        _log_receipt(workspace_root, receipt, fired_via=fired_via,
                     source_skill=source_skill)
        return receipt

    normalized = []
    drops: dict = {}
    for raw_msg in chat_messages or []:
        m = normalize_chat_message(raw_msg, provider=provider,
                                   user_chat_ids=user_chat_ids,
                                   user_names=user_names, diagnostics=drops)
        if m is not None:
            normalized.append(m)
    n_raw = len(list(chat_messages or []))

    candidates, counts, deferred = select_candidates(normalized)
    counts["n_scanned"] = n_raw
    counts["n_dropped_noise"] = int(drops.get("noise", 0))
    counts["n_dropped_bad_ts"] = int(drops.get("bad_ts", 0))

    from cru_match import load_open_commitments
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    opens = load_open_commitments(str(events_path), workspace_root=workspace_root)

    scored = score_candidates(
        opens, candidates, user_person_id=user_person_id, provider=provider,
        exclude_captured_since=exclude_captured_since,
        workspace_root=workspace_root)

    closed, proposed, refused = _apply(
        workspace_root, scored, provider=provider, source_skill=source_skill)
    counts["n_closed"] = len(closed)
    counts["n_proposed"] = len(proposed)
    counts["n_refused_no_pointer"] = len(refused)

    # THE CURSOR MAY NOT PASS WHAT THIS FIRE DID NOT ADJUDICATE.
    #
    # Everything normalized was SEEN, and everything that reached the matcher
    # was DECIDED — including the chatter the relevance bound excluded, which
    # is a decision, not a deferral. What must hold the cursor back is the
    # over-cap remainder: real candidates this fire chose not to score. They
    # are, by the oldest-first drain above, the NEWEST survivors, so holding
    # the cursor at the newest ADJUDICATED candidate leaves exactly them (and
    # nothing else) inside the next fire's window.
    #
    # Before this, the cursor took the newest message seen and the deferred
    # set was permanently behind it, while `_summary` told the user in as many
    # words that those messages would "get picked up next time". The receipt
    # was making a promise the cursor had already foreclosed.
    cursor_after = cursor_before
    ceiling = max((m["ts"] for m in normalized if m.get("ts")), default=None)
    if deferred:
        adjudicated = [m["ts"] for m in candidates if m.get("ts")]
        # No candidate scored at all (cap 0 or every candidate deferred) →
        # the cursor does not move. A fire that adjudicated nothing has
        # earned no ground.
        ceiling = max(adjudicated) if adjudicated else None
    if ceiling and (cursor_before is None or ceiling > cursor_before):
        cursor_after = ceiling
        _write_cursor(workspace_root, cursor_after, source_skill=source_skill)

    receipt = {
        "ran": True,
        "status": "degraded" if plan.get("degraded") else "complete",
        "leg": JOB_ID,
        "provider": provider,
        "scan_mode": plan.get("mode"),
        "degraded": bool(plan.get("degraded")),
        "coverage_note": plan.get("coverage_note"),
        "scan_limits": plan.get("limits") or {},
        "cursor_before": cursor_before,
        "cursor_after": cursor_after,
        "cursor_advanced": cursor_after != cursor_before,
        # The deferred remainder, held INSIDE the next fire's window by the
        # cursor above. Recorded so the promise on the summary is checkable
        # against the substrate rather than taken on trust.
        "n_deferred": len(deferred),
        "deferred_from_ts": (min((m["ts"] for m in deferred if m.get("ts")),
                                 default=None) if deferred else None),
        # A partial connector sweep is a DIFFERENT kind of gap from an
        # over-cap deferral, and conflating them would be the dishonest half
        # of F-1: re-running the same window on this backend degrades the same
        # way, so those messages are not "picked up next time" — they are
        # outside what this connector will ever return for this window. The
        # cursor still advances (otherwise the leg never progresses) and the
        # receipt records that it advanced over an admittedly partial sweep.
        "cursor_advanced_over_partial_sweep": bool(
            plan.get("degraded") and cursor_after != cursor_before),
        "closed": closed,
        "proposed": proposed,
        "refused_no_pointer": refused,
    }
    receipt.update(counts)
    receipt["summary"] = _summary(receipt)
    _log_receipt(workspace_root, receipt, fired_via=fired_via,
                 source_skill=source_skill)
    return receipt


def _apply(workspace_root, scored: dict, *, provider: str, source_skill: str):
    """Write the closes and the close-proposals.

    Both go through the SAME writers mail uses — `commitment_state.
    close_commitments` for closes and `cru_match.build_pending_review_event`
    for proposals — so a chat-originated row reaches the needs-your-call
    queue and the staff-meeting fold through the shared adapters, the shared
    card builder and the shared fences. There is deliberately no chat-specific
    surface, no chat-specific event type, and no chat-specific renderer: a
    second pile is a second thing to remember to look at.
    """
    from commitment_state import close_commitments
    from cru_match import build_pending_review_event, load_open_review_proposals
    from event_gate import append_event

    by_native = scored.get("by_native_id") or {}
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"

    refused: List[dict] = []
    to_close: List[dict] = []
    ref_by_cid: Dict[str, dict] = {}

    for row in scored.get("auto_close") or []:
        msg = by_native.get(str(row.get("message_id") or ""))
        ref = None
        if msg is not None:
            try:
                ref = build_ref(msg)
            except _chat.ChatPointerError:
                ref = None
        if ref is None:
            # THE fence. A close whose pointer cannot be built is not written,
            # is counted, and is named — never downgraded into a quiet drop.
            refused.append({"commitment_id": row.get("commitment_id"),
                            "why": "no complete pointer back to the message"})
            continue
        cid = str(row.get("commitment_id"))
        ref_by_cid[cid] = ref
        to_close.append({
            "commitment_id": cid,
            "resolved_by": "chat_reconcile",
            "evidence": (row.get("evidence") or "matched a message you sent"),
            "primary_thread_id": row.get("primary_thread_id") or "",
            # `pointer_fields` emits BOTH spellings of the one pointer, so a
            # writer physically cannot produce one half without the other.
            # `close_commitment` merges extra_data without letting it override
            # the canonical closure keys.
            "extra_data": _chat.pointer_fields(ref),
        })

    closed: List[dict] = []
    if to_close:
        results = close_commitments(workspace_root, to_close,
                                    source_skill=source_skill)
        for r in results:
            if r.get("status") != "closed":
                continue
            # VERIFY THE WRITE, don't trust the call. The contract of this leg
            # is that a chat close is auditable, and the only proof of that is
            # the event that actually landed — an `extra_data` channel that
            # silently stopped carrying keys would otherwise produce exactly
            # the unauditable closes this fence exists to prevent, with every
            # call site still looking correct.
            #
            # It RAISES rather than degrading, and the fire dies without a
            # receipt — so the job stays due and the failure is impossible to
            # miss. That is deliberate: an unauditable close is already on
            # disk at this point, and the only worse outcome than stopping is
            # continuing to write more of them behind a healthy-looking
            # summary (the Bug #102 abort posture, one lane over).
            assert_pointer_or_refuse(r.get("event"))
            closed.append({
                "commitment_id": r.get("commitment_id"),
                "source_ref": _chat.chat_ref_key(
                    _chat.read_chat_source_ref(r.get("event"))),
            })

    proposed: List[dict] = []
    pending = scored.get("pending") or []
    if pending:
        already = {(p.get("data") or {}).get("commitment_id")
                   for p in load_open_review_proposals(str(events_path))}
        for row in pending:
            cid = str(row.get("commitment_id"))
            if cid in already:
                continue
            msg = by_native.get(str(row.get("message_id") or ""))
            ref = None
            if msg is not None:
                try:
                    ref = build_ref(msg)
                except _chat.ChatPointerError:
                    ref = None
            if ref is None:
                refused.append({"commitment_id": cid,
                                "why": "no complete pointer back to the message"})
                continue
            ev = build_pending_review_event(
                commitment_id=cid,
                primary_thread_id=row.get("primary_thread_id") or "",
                source_skill=source_skill,
                proposed_resolution="auto_resolve",
                score=row.get("score") or 0,
                evidence=row.get("evidence") or "matched a message in chat",
                next_seq=None,
                title=row.get("title") or "",
                # The message's own time. The shared bulk-accept fence orders
                # evidence against the promise at apply time — but only if the
                # timestamp was persisted, so a hand-built row would screen as
                # STRONG by construction (the WATCHGATE N-2 class).
                evidence_ts=row.get("ts") or None,
                has_completion_signal=None,
            )
            ev["data"].update(_chat.pointer_fields(ref))
            # Same post-write verification the close branch runs. A proposal
            # is the row a human adjudicates, so its pointer is the thing that
            # lets them check the claim before confirming — an unauditable
            # proposal asks somebody to take the match on faith. The review
            # noted only closes were verified; both are now.
            assert_pointer_or_refuse(dict(ev, type="commitment_resolved"))
            append_event(events_path, [ev], holder=source_skill)
            proposed.append({"commitment_id": cid,
                             "source_ref": _chat.chat_ref_key(ref)})

    return closed, proposed, refused


def assert_pointer_or_refuse(event: Optional[dict]) -> None:
    """Raise `chat.ChatPointerError` when a chat-evidenced closure event has no
    complete pointer. The READ-side half of "no pointer, no close" — applied
    to history this module did not write (an integrity pass, a review), where
    the write-time refusal above cannot reach."""
    reason = _chat.missing_pointer_reason(event)
    if reason:
        raise _chat.ChatPointerError(
            f"a chat-evidenced closure is unauditable: {reason}")


def is_chat_evidenced(event: Optional[dict]) -> bool:
    """True for a closure this leg produced. Keyed on `resolved_by`, which is
    the field the closure path itself writes — not on the pointer, because the
    whole point of the check is to find rows whose pointer is MISSING."""
    if not isinstance(event, dict):
        return False
    if event.get("type") not in ("commitment_resolved", "thread_resolved"):
        return False
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return str(data.get("resolved_by") or "").strip() == "chat_reconcile"


# ---------------------------------------------------------------------------
# Receipt + validator
# ---------------------------------------------------------------------------

def _summary(receipt: dict) -> str:
    """The line a surface may paste verbatim. Plain language, no field names,
    and it NEVER claims coverage the scan did not have."""
    n_scanned = receipt.get("n_scanned") or 0
    n_closed = receipt.get("n_closed") or 0
    n_prop = receipt.get("n_proposed") or 0
    if n_scanned == 0:
        line = "No new chat since the last check — nothing to reconcile."
    elif n_closed == 0 and n_prop == 0:
        line = (f"Checked {n_scanned} chat message"
                f"{'s' if n_scanned != 1 else ''} — nothing matched an open "
                "commitment.")
    else:
        bits = []
        if n_closed:
            bits.append(f"closed {n_closed} you'd already handled")
        if n_prop:
            bits.append(f"{n_prop} to confirm")
        line = "Reconciled your chat: " + ", ".join(bits) + "."
    if receipt.get("n_candidates_over_cap"):
        # This sentence is only true because the cursor is held behind the
        # deferred set (see the cursor block in reconcile_chat_and_receipt).
        # It used to ship over a cursor that had already passed them.
        n = receipt["n_candidates_over_cap"]
        line += (f" {n} newer message{'s' if n != 1 else ''} went past this "
                 "pass's limit and are picked up on the next one.")
    if receipt.get("n_dropped_bad_ts"):
        n = receipt["n_dropped_bad_ts"]
        line += (f" {n} message{'s' if n != 1 else ''} arrived without a "
                 "usable date and could not be matched to anything.")
    if receipt.get("n_refused_no_pointer"):
        n = receipt["n_refused_no_pointer"]
        line += (f" {n} possible match{'es' if n != 1 else ''} were left open "
                 "because there was no way to link back to the message that "
                 "would have closed them.")
    if receipt.get("coverage_note"):
        line += " " + receipt["coverage_note"]
    return line


_RECEIPT_KEYS = ("provider", "scan_mode", "degraded", "coverage_note",
                 "scan_limits", "cursor_before", "cursor_after",
                 "skip_reason", "blocked_reason",
                 "n_deferred", "deferred_from_ts",
                 "cursor_advanced_over_partial_sweep",
                 "n_scanned", "n_dropped_noise", "n_dropped_bad_ts",
                 "n_dropped_direction",
                 "n_candidates", "n_candidates_over_cap", "n_closed",
                 "n_proposed", "n_refused_no_pointer")


def _log_receipt(workspace_root, receipt: dict, *, fired_via: str,
                 source_skill: str) -> None:
    """ONE `chat_reconcile` audit event per fire, through the canonical
    receipt writer — including for a skip and for a blocked run.

    Every terminal state writes. A leg that stays silent when it does not run
    is a leg nobody can prove ran, and "no chat backend" then reads exactly
    like "swept everything and found nothing"."""
    from receipts import log_receipt
    extra = {k: receipt.get(k) for k in _RECEIPT_KEYS if receipt.get(k) is not None}
    extra["cursor_from"] = receipt.get("cursor_before")
    extra["cursor_to"] = receipt.get("cursor_after")
    try:
        log_receipt(workspace_root, JOB_ID, receipt_type=RECEIPT_EVENT_TYPE,
                    status=receipt.get("status") or "complete",
                    fired_via=fired_via, surfaced=receipt.get("n_closed") or 0,
                    extra_data=extra)
    except Exception as exc:  # noqa: BLE001 — a receipt failure must be VISIBLE
        # stderr alone is not loud on a scheduled fire — nobody reads it. The
        # returned dict is what a caller acts on, so the failure is stamped
        # THERE too. It does not raise: no receipt means the job stays due and
        # self-heals on the next fire, which is the right outcome; what was
        # wrong was returning a receipt that looked healthy while no proof of
        # the run existed.
        print(f"chat_reconcile: receipt FAILED to land ({type(exc).__name__}: "
              f"{exc}) — this fire cannot be proven to have run",
              file=sys.stderr)
        receipt["receipt_failed"] = True
        receipt["receipt_error"] = f"{type(exc).__name__}: {exc}"


def validate_chat_reconcile_ran(workspace_root, *, since_cursor=None) -> dict:
    """Read the newest `chat_reconcile` audit back and say whether a real chat
    pass happened.

    A SKIPPED run returns `ok=True, ran=False`: the leg correctly did nothing
    because there is no chat backend, and reporting that as a failure would
    have every mail-only workspace's maintenance run go permanently red. A
    BLOCKED run returns `ok=False` — a read that could not happen is not a
    read that found nothing, and its zero means nothing.
    """
    from receipts import iter_receipts

    newest = None
    for r in iter_receipts(workspace_root, task_ids=[JOB_ID]):
        if r.get("type") == RECEIPT_EVENT_TYPE:
            newest = r
    if newest is None:
        return {"ok": False, "ran": False,
                "reason": "no chat_reconcile audit event — the chat leg did "
                          "not run"}
    data = newest["raw"].get("data") if isinstance(newest["raw"].get("data"), dict) else {}
    status = data.get("status")
    if status == "skipped":
        return {"ok": True, "ran": False, "status": "skipped",
                "reason": data.get("skip_reason"),
                "n_closed": 0, "scan_mode": data.get("scan_mode")}
    if status == "blocked":
        return {"ok": False, "ran": False, "status": "blocked",
                "reason": ("the chat read did not happen — "
                           + (data.get("blocked_reason") or "recorded as blocked")),
                "cursor_from": data.get("cursor_from"),
                "cursor_to": data.get("cursor_to")}
    if since_cursor is not None and data.get("cursor_from") != since_cursor:
        return {"ok": False, "ran": True,
                "reason": (f"latest audit is from a prior run "
                           f"(cursor_from={data.get('cursor_from')!r} != "
                           f"expected {since_cursor!r})")}
    return {
        "ok": True, "ran": True, "status": status,
        "cursor_from": data.get("cursor_from"),
        "cursor_to": data.get("cursor_to"),
        "scan_mode": data.get("scan_mode"),
        "degraded": bool(data.get("degraded")),
        "coverage_note": data.get("coverage_note"),
        "n_scanned": data.get("n_scanned"),
        "n_closed": data.get("n_closed"),
        "n_proposed": data.get("n_proposed"),
    }


__all__ = [
    "JOB_ID", "RECEIPT_EVENT_TYPE", "CURSOR_KEY",
    "DEFAULT_BACKFILL_DAYS", "MESSAGE_CAP", "CANDIDATE_CAP",
    "ChatReconcileError",
    "normalize_chat_message", "build_ref", "select_candidates",
    "score_candidates", "reconcile_chat_and_receipt",
    "read_cursor", "backfill_floor",
    "assert_pointer_or_refuse", "is_chat_evidenced",
    "validate_chat_reconcile_ran",
]
