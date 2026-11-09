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


def _effective_fired_via(explicit):
    """`receipts.effective_fired_via`, behind an import that cannot break."""
    try:
        from receipts import effective_fired_via
    except Exception:  # noqa: BLE001 - a resolver that raises is worse
        return explicit if explicit is not None else "scheduled"
    try:
        return effective_fired_via(explicit)
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"

JOB_ID = "reconcile-chat"
RECEIPT_EVENT_TYPE = "chat_reconcile"
#: On a chat review proposal: which leg wrote it, whatever became of its
#: pointer (cr1#164, `is_chat_proposal`).
PROPOSED_BY = "chat_reconcile"
CURSOR_KEY = "chat_reconcile_cursor"
SOURCE_SKILL = "reconcile-sent"

# First-declaration backfill horizon (§3: "a short fixed window, builder picks,
# receipted, never full history"). cr1#663 (the cr1#609 ruling of
# 2026-11-03): the one bound every maintenance job shares, 30 days, taken
# from the one helper rather than typed here. It was seven days, matched to
# the Slack capture leg's DEFAULT_WINDOW_DAYS; the ruling sets 30 for every
# job, and a cursor older than the bound reads as a first run too.
from catchup import MAINTENANCE_WINDOW_DAYS as DEFAULT_BACKFILL_DAYS  # noqa: E402
from catchup import MAINTENANCE_WINDOW_FIELD  # noqa: E402

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
        "n_excluded": 0,
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

#: CHATAUTHOR1 (cr1#244): every spelling of "who wrote this" the normaliser
#: reads, in order. `user_id` is the plan's own name for it (the staged shape
#: the fires hand this leg, `CHAT_MESSAGE_FIELDS`); `author_id`, `user` and
#: `from_id` are the raw connector spellings. Before this the staged shape's
#: author was never read, so an owner's chat reply could never be the owner's
#: and nothing it discharged ever closed.
CHAT_AUTHOR_KEYS = ("author_id", "user_id", "user", "from_id")


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

    author = next((_norm(msg.get(k)) for k in CHAT_AUTHOR_KEYS if _norm(msg.get(k))), "")
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


def _people_names(workspace_root) -> Dict[str, List[str]]:
    """person id -> every name on file for that person (name, canonical name,
    aliases). Read-only; an unreadable entity graph answers `{}`, which leaves
    only the names the message and the commitment carry themselves."""
    if workspace_root is None:
        return {}
    try:
        import json
        from entities_io import entities_collection
        raw = json.loads(_entities_path(workspace_root).read_text(encoding="utf-8"))
        out: Dict[str, List[str]] = {}
        for p in entities_collection(raw, "people"):
            pid = _norm(p.get("id"))
            if not pid:
                continue
            labels = [p.get("name"), p.get("canonical_name")]
            labels += list(p.get("aliases") or [])
            out[pid] = [_norm(x) for x in labels if _norm(x)]
        return out
    except Exception:  # noqa: BLE001 - a name lookup must never raise into the leg
        return {}


def _require_shared_words(auto: List[dict], pending: List[dict],
                          open_commitments, by_native: Dict[str, dict], *,
                          user_person_id: str, workspace_root=None):
    """cr1#468 (owner ruling 2026-10-27, option B): a chat line that shares no
    words with a commitment can never close it.

    THE DEFECT. The title matcher counts every shared token. On a short chat
    line the only shared token can be a person's name (every message to
    someone "mentions" them, and a namesake gets the same token) or the stem a
    contraction leaves ("won't" tokenizes to "won"). One such token cleared the
    propose bar, FS-11 promoted the unambiguous moderate match, and an owner
    aside closed an unrelated promise as "probably handled".

    THE RULE. Each row the two seams graded is checked against its own
    message: `cru_match.shared_content_words` with the names of the user, the
    people the message went to and the commitment's counterparties set aside.
    A row whose answer is empty is dropped, whatever basis graded it (the
    title echo, a delivery, a reply or the thread the item came from): it
    neither closes nor is proposed, as the issue's acceptance check reads.
    Rows that share a word pass untouched, so FS-11's weak-match closes stand
    exactly as before (the 2026-09-07 "chat is your own word" ruling).

    ONLY NARROWS. It runs after both seams and only removes rows, so it never
    adds a row, never changes a grade and never touches the mail rail. A row whose message
    cannot be found is left as the seams graded it: the pointer fence in
    `_apply` already refuses a close it cannot point at.
    """
    from commitment_parties import counterparty_ids, counterparty_names
    from cru_match import _commitment_id, shared_content_words

    people = _people_names(workspace_root)
    by_cid = {str(_commitment_id(c)): c for c in open_commitments or []
              if isinstance(c, dict)}

    def _names(row: dict, msg: dict) -> List[str]:
        out: List[str] = list(people.get(_norm(user_person_id), []))
        out += list(msg.get("counterparty_names") or [])
        for pid in msg.get("counterparty_person_ids") or []:
            out += people.get(_norm(pid), [])
        ev = by_cid.get(str(row.get("commitment_id") or ""))
        if ev is not None:
            data = ev.get("data") or {}
            out += counterparty_names(data, workspace_root=workspace_root)
            for pid in counterparty_ids(data) + [_norm(data.get("owner_id"))]:
                out += people.get(_norm(pid), [])
        return out

    def _shares(row: dict) -> bool:
        msg = by_native.get(str(row.get("message_id") or ""))
        if msg is None:
            return True
        return bool(shared_content_words(msg.get("text"), row.get("title"),
                                         names=_names(row, msg)))

    return ([row for row in auto if _shares(row)],
            [row for row in pending if _shares(row)])


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
            # CLOSETRUTH1 fix round 2 (review F-13) — say what this rail is
            # instead of letting the matcher guess it from a field. The proof
            # floor on the bare-title path is M's SENT-MAIL ruling; a chat
            # message is the person's own typed word, which the 2026-09-07
            # ruling already counts as evidence, and no ruling puts a second
            # bar on that door. Round 1 let the `subject: None` built in
            # `_to_matcher_shape` stand in for this fact — which also let a
            # subject-less MAIL through the very same hole.
            from_mail=False,
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

    auto, pending = _require_shared_words(
        auto, pending, open_commitments, by_native,
        user_person_id=user_person_id, workspace_root=workspace_root)

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
    """The ISO instant a chat fire reads back to.

    `(floor_iso, is_first_run)`. The stored cursor when it is inside the
    bound; otherwise the last `days` (30, the one maintenance window bound)
    and `is_first_run` True: a first declaration reads a fixed window and
    says so on its receipt, never full history (the passive-ingestion
    failure mode this build is explicitly not), and a cursor older than the
    bound is treated as a first run the same way (cr1#663)."""
    cursor, _raw = read_cursor(workspace_root)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    floor = now - _dt.timedelta(days=days)
    if cursor:
        try:
            cur = _dt.datetime.fromisoformat(str(cursor).replace("Z", "+00:00"))
            if cur.tzinfo is None:
                cur = cur.replace(tzinfo=_dt.timezone.utc)
            if cur >= floor:
                return cursor, False
        except ValueError:
            pass
    return floor.isoformat(), True


def _window_for(cursor, *, now=None) -> dict:
    """The chat leg's maintenance window from its own cursor (cr1#663), the
    same math every job uses (`catchup.window_from_cursor`)."""
    from catchup import window_from_cursor

    local_now = None
    if isinstance(now, _dt.datetime):
        local_now = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    return window_from_cursor(JOB_ID, cursor, now=local_now)


# ---------------------------------------------------------------------------
# The orchestrator + the receipt
# ---------------------------------------------------------------------------

#: MAINTJOBS1 MUST 2 - what a raw chat message carries into the writer
#: (Step 6b of `skills/reconcile-sent/SKILL.md`); the leg normalizes both id
#: shapes itself, so these are the names the plan hands the connector step.
#: CHATAUTHOR1 (cr1#244): the kept shape carries both author spellings this
#: leg reads (`user_id`, the plan's; `author_id`, the raw one) and the
#: counterparty fields the scorer reads, so a staged message can still say
#: who wrote it and to whom. The last two are the channel's name, which only
#: `_room_names` reads, so a list written in words matches a staged message
#: (cr1#770). `_room_names`' id spellings (`channel_id`, `channel`, `chat_id`)
#: stay out: the normaliser reads those as the room, so keeping them would
#: score a message the staged path drops today. So does its bare `name`: it
#: says nothing about whose name it is, and a person's must not match a list.
CHAT_MESSAGE_FIELDS = ("chat_or_channel_id", "message_id", "ts", "user_id",
                       "author_id", "user_name", "text", "thread_ts", "permalink",
                       "counterparty_person_ids", "counterparty_names",
                       "channel_name", "chat_name")


def _connector_read(workspace_root, purpose: str):
    """cr1#667: the declared chat connector's dials and lists for `purpose`
    (`connector_reads.for_category`), or None when none is declared or the
    reader is not on the tree. Never raises."""
    try:
        from connector_reads import for_category
        return for_category(workspace_root, _chat.CATEGORY, purpose=purpose)
    except Exception:  # noqa: BLE001
        return None


def _room_names(raw_msg) -> list:
    """Every spelling of the channel a raw message sits in: its id, by the
    same keys the normaliser reads, and the connector's display name for it
    when the message carries one, so a channel the owner named in words
    matches (cr1#667 review)."""
    if not isinstance(raw_msg, dict):
        return []
    out = []
    for key in ("chat_or_channel_id", "channel_id", "channel", "chat_id",
                "channel_name", "chat_name", "name"):
        v = raw_msg.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v)
    return out


def plan_chat_scan(workspace_root, now=None) -> dict:
    """The reconcile-chat job PLANNED beside the data - READ ONLY (MAINTJOBS1
    MUST 2; on `workspace_access.RUN_HELPER_ALLOWLIST`).

    Step 6a/6b of the reconcile-sent skill, answered where the ledger is: the
    declared chat backend (`resolve_chat_provider` - None is "no backend",
    never a default), the scan plan (`plan_scan`: mode, degraded, coverage
    note), the read floor (`backfill_floor`: the cursor, or a short fixed
    window on a first run), the connector's label for the host tool search,
    the primary user, and the fire start. `provider: null` is not a stop: the
    writer still runs with no messages and lands the SKIP receipt, because a
    silent skip with no record reads like a sweep that found nothing.
    `ready` is false only when a backend is declared and no primary user
    resolves. Writes nothing."""
    import datetime as _dtm

    now = now or _dtm.datetime.now(_dtm.timezone.utc)
    if isinstance(now, str):
        now = _dtm.datetime.fromisoformat(now.replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.astimezone()
    try:
        provider = _chat.resolve_chat_provider(workspace_root)
    except Exception:  # noqa: BLE001
        provider = None
    try:
        declared = _chat.declared_chat_backend(workspace_root) or {}
    except Exception:  # noqa: BLE001
        declared = {}
    scan = _chat.plan_scan(provider, date_filtered=True)
    try:
        floor, first_run = backfill_floor(workspace_root, now=now)
    except Exception:  # noqa: BLE001
        floor, first_run = None, True
    try:
        cursor, _raw = read_cursor(workspace_root)
    except Exception:  # noqa: BLE001
        cursor = None
    try:
        from primary_user import resolve_primary_user
        user = resolve_primary_user(workspace_root)
    except Exception:  # noqa: BLE001
        user = None
    from catchup import MAINTENANCE_WINDOW_FIELD

    label = str(declared.get("label") or "").strip()
    # cr1#667: the dials and the lists are read BEFORE the source. A
    # `skip_reason` means the connector step stages nothing and the writer
    # lands the skip receipt; `include` / `exclude` are the channels the
    # connector step reads, and the ones it never reads.
    read = _connector_read(workspace_root, "close")
    skip_reason = (read or {}).get("skip_reason")
    return {
        "job_id": JOB_ID,
        "provider": provider,
        "scan_plan": scan,
        "after": floor,
        "first_run": bool(first_run),
        # cr1#663: the window this leg reads, from the one helper; `after`
        # above is its `since` on the cursor branch and the 30-day floor on
        # a first run or a capped gap.
        MAINTENANCE_WINDOW_FIELD: _window_for(cursor, now=now),
        "connector_queries": ([label] if (provider and label and not skip_reason)
                              else []),
        "connector_read": read,
        "include": list((read or {}).get("include") or []),
        "exclude": list((read or {}).get("exclude") or []),
        "skip_reason": skip_reason,
        "user_person_id": user,
        "message_fields": list(CHAT_MESSAGE_FIELDS),
        "fire_start": now.astimezone(_dtm.timezone.utc).isoformat(),
        "ready": bool(user) or not provider,
        "blocked_reason": (None if (user or not provider) else
                           "the workspace's own person is not recorded"),
    }


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
    fired_via=None,
    fetch_blocked=None,
    exclude_captured_since=None,
    read_complete=None,
) -> dict:
    """Run the chat closure leg end-to-end and return a receipt whose fields
    exist only because the work ran.

    `read_complete` (cr1#742): did the read reach the end of its window?
    False is a PARTIAL read (more pages than the run read): the batch is
    scored, the cursor does NOT advance over the messages never seen, and
    the receipt says `read_partial`. True, or None from a caller that
    predates the flag, is a read of the whole window (a `per_chat_scan`'s
    own partial cover is `degraded`, a different thing, and unchanged).

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
    # MAINTJOBS1 MUST 2 - THE WRITER IS NAMED FIRST. `_log_receipt` swallows
    # a receipt failure (on purpose: no receipt keeps the job due), which
    # means a refused identity there came AFTER this leg's closes and cursor
    # write had landed unattributed. Asked here, the refusal comes before any
    # of them, in the one sentence.
    from receipts import require_writer_identity

    require_writer_identity(workspace_root=workspace_root)
    # FIX3 F3-6: a literal default IS an explicit value by the time the
    # resolver sees it (the FIX2 M-3 lesson), so this signature says
    # nothing and the seat answers. A legacy or local seat still reads
    # `scheduled`, byte for byte; a merged seat with nothing forwarded
    # reads `manual`, which is what a typed brief actually is.
    fired_via = _effective_fired_via(fired_via)
    provider = _chat.resolve_chat_provider(workspace_root, provider)
    # cr1#663: the window this run was asked to cover, from the leg's own
    # cursor, named on every terminal receipt below (skip, blocked, run).
    # Computed BEFORE anything writes, so the basis is the previous run.
    try:
        _cursor_now, _raw_now = read_cursor(workspace_root)
    except Exception:  # noqa: BLE001
        _cursor_now = None
    window = _window_for(_cursor_now)
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
        receipt[MAINTENANCE_WINDOW_FIELD] = window
        _log_receipt(workspace_root, receipt, fired_via=fired_via,
                     source_skill=source_skill)
        return receipt

    # cr1#667: the dials first. A connector the owner turned off for closes
    # is skipped the way an undeclared backend is: silently, and receipted
    # so the two never look alike ("turned off" is not "swept and found
    # nothing"). The exclude list is applied below, before any match.
    read = _connector_read(workspace_root, "close")
    if read and read.get("skip_reason"):
        receipt = _chat.skip_receipt(str(read["skip_reason"]), provider=provider,
                                     leg=JOB_ID)
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
            MAINTENANCE_WINDOW_FIELD: window,
        }
        receipt.update(_empty_counts())
        _log_receipt(workspace_root, receipt, fired_via=fired_via,
                     source_skill=source_skill)
        return receipt

    normalized = []
    drops: dict = {}
    # cr1#667: a channel on the exclude list is never read. The plan hands
    # the lists to the connector step so the read itself skips them; this is
    # the belt for a staged message from one anyway, dropped before any
    # match and counted on the receipt. A message a list turned away has been
    # read, so the cursor passes it (`aside_ts`), named or not: an id-based
    # include list or a one-to-one chat can never supply a name, and a cursor
    # waiting for one starves everything behind it (cr1#770).
    n_excluded = 0
    aside_ts: List[str] = []
    kept_messages = []
    try:
        from connector_reads import allowed as _allowed
    except Exception:  # noqa: BLE001
        _allowed = None
    for raw_msg in chat_messages or []:
        if read and _allowed is not None and not _allowed(read, *_room_names(raw_msg)):
            n_excluded += 1
            seen = normalize_chat_message(raw_msg, provider=provider,
                                          user_chat_ids=user_chat_ids,
                                          user_names=user_names)
            if seen is not None:
                aside_ts.append(seen["ts"])
            continue
        kept_messages.append(raw_msg)
    for raw_msg in kept_messages:
        m = normalize_chat_message(raw_msg, provider=provider,
                                   user_chat_ids=user_chat_ids,
                                   user_names=user_names, diagnostics=drops)
        if m is not None:
            normalized.append(m)
    n_raw = len(list(chat_messages or []))

    candidates, counts, deferred = select_candidates(normalized)
    counts["n_scanned"] = n_raw
    counts["n_excluded"] = n_excluded
    counts["n_dropped_noise"] = int(drops.get("noise", 0))
    counts["n_dropped_bad_ts"] = int(drops.get("bad_ts", 0))

    from cru_match import load_open_commitments
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    opens = load_open_commitments(str(events_path), workspace_root=workspace_root)

    scored = score_candidates(
        opens, candidates, user_person_id=user_person_id, provider=provider,
        exclude_captured_since=exclude_captured_since,
        workspace_root=workspace_root)

    # cr1#756 - the chat connector's close mode: under `propose` every
    # matched close joins the confirm list with its reason.
    close_mode = str(((read or {}).get("dials") or {}).get("close_mode")
                     or "").strip().lower() or None
    closed, proposed, refused = _apply(
        workspace_root, scored, provider=provider, source_skill=source_skill,
        close_mode=close_mode)
    counts["n_closed"] = len(closed)
    counts["n_proposed"] = len(proposed)
    counts["n_refused_no_pointer"] = sum(1 for r in refused if not r.get("close_mode"))
    counts["n_close_mode_refused"] = sum(1 for r in refused if r.get("close_mode"))
    counts["close_mode"] = close_mode
    counts["n_close_mode_proposed"] = sum(1 for p in proposed if p.get("close_mode"))

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
    passed = [m["ts"] for m in normalized if m.get("ts")] + aside_ts
    if deferred:
        # No candidate scored at all (cap 0 or every candidate deferred) →
        # the cursor does not move. A fire that adjudicated nothing has
        # earned no ground.
        passed = [m["ts"] for m in candidates if m.get("ts")]
    ceiling = max(passed, default=None)
    from catchup import read_reached_end
    read_partial = read_reached_end(read_complete) is False
    if read_partial:
        # cr1#742: a partial read earns no ground; the next run reads the
        # same window and the dedup keeps the re-read cheap.
        ceiling = None
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
        # cr1#742: the read did not reach the end of its window
        "read_partial": read_partial,
        "closed": closed,
        "proposed": proposed,
        "refused_no_pointer": [r for r in refused if not r.get("close_mode")],
        "close_mode_refused": [r for r in refused if r.get("close_mode")],
        MAINTENANCE_WINDOW_FIELD: window,
    }
    receipt.update(counts)
    receipt["summary"] = _summary(receipt)
    _log_receipt(workspace_root, receipt, fired_via=fired_via,
                 source_skill=source_skill)
    return receipt


def _propose_one(row: dict, already: set, asked: set, by_native: dict,
                 proposed: List[dict], refused: List[dict], *, events_path,
                 source_skill: str) -> None:
    """One close-proposal, written through the shared writer. A row with no
    pointer back to its message is refused, by name."""
    from cru_match import build_pending_review_event
    from event_gate import append_event

    cid = str(row.get("commitment_id"))
    if cid in already:
        return
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
        return
    # cr1#756 - a dial's proposal is asked once per message, ever.
    demoted = bool(row.get("close_mode_reason"))
    if demoted and (cid, _chat.chat_ref_key(ref)) in asked:
        return
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
    # cr1#164: says this came from chat when the pointer does not.
    ev["data"]["proposed_by"] = PROPOSED_BY
    if demoted:
        # The reason, and the stamp that keeps the timeout from
        # closing what the owner asked to be asked about.
        ev["data"]["close_mode_reason"] = row["close_mode_reason"]
        ev["data"]["auto_close_blocked"] = True
    # BIND1 STOPPED SHORT HERE, ON PURPOSE. This leg writes no OPEN
    # commitment row - it closes, and it proposes - so the only row
    # it could bind is this review proposal. Importing the capture
    # door to do it reds G26's scope fence
    # (`tests/run_guard_chatscan1_wiring_test.py`, "no chat leg
    # imports the capture writer"), which exists to keep chat CAPTURE
    # unbuilt; and reaching past that door into the resolver directly
    # would be the same trespass spelled differently. The binding
    # belongs one level down, inside
    # `cru_match.build_pending_review_event` - the SHARED writer both
    # this leg and the mail rail already call - which is not this
    # lane's file. Recorded as a seam in BUILD_BIND1_2026-09-18.md.
    # Same post-write verification the close branch runs. A proposal
    # is the row a human adjudicates, so its pointer is the thing that
    # lets them check the claim before confirming — an unauditable
    # proposal asks somebody to take the match on faith. The review
    # noted only closes were verified; both are now.
    assert_pointer_or_refuse(dict(ev, type="commitment_resolved"))
    append_event(events_path, [ev], holder=source_skill)
    proposed.append({"commitment_id": cid,
                     "source_ref": _chat.chat_ref_key(ref),
                     **({"close_mode": True} if demoted else {})})


def _apply(workspace_root, scored: dict, *, provider: str, source_skill: str,
           close_mode: Optional[str] = None):
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
    from cru_match import load_open_review_proposals

    by_native = scored.get("by_native_id") or {}
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"

    refused: List[dict] = []
    to_close: List[dict] = []
    ref_by_cid: Dict[str, dict] = {}

    # cr1#756 - under `propose` the matched closes are proposals, each
    # carrying the reason; nothing below closes.
    auto_rows = list(scored.get("auto_close") or [])
    pending_rows = list(scored.get("pending") or [])
    asked: set = set()
    if close_mode == "propose":
        try:
            from close_mode_dial import PROPOSE_REASON as _PROPOSE_REASON
            from close_mode_dial import asked_evidence
            asked = asked_evidence(workspace_root)
        except Exception:  # noqa: BLE001
            _PROPOSE_REASON = "this connector is set to ask before closing"
        for row in auto_rows:
            demoted = dict(row)
            demoted["recommendation"] = "pending_review"
            demoted["close_mode_reason"] = _PROPOSE_REASON
            pending_rows.append(demoted)
        auto_rows = []

    for row in auto_rows:
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
            # PROV1 — the pointer travels as a FIRST-CLASS argument now, so
            # this leg stops depending on `extra_data` passthrough to carry
            # the one field the close's auditability rests on.
            "source_ref": _chat.source_ref_string(ref),
            # `pointer_fields` emits BOTH spellings of the one pointer, so a
            # writer physically cannot produce one half without the other.
            # `close_commitment` merges extra_data without letting it override
            # the canonical closure keys — the STRUCTURED half
            # (`chat_source_ref`) is what `assert_pointer_or_refuse` verifies
            # after the write, and only extra_data carries it.
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
    pending = pending_rows
    if pending:
        already = {(p.get("data") or {}).get("commitment_id")
                   for p in load_open_review_proposals(str(events_path))}
        for row in pending:
            # cr1#756 - under `propose` the batch is the owner's whole confirm
            # list: one row the writer or the gate refuses is counted and
            # costs only itself, as `close_mode_dial.propose_closes` does.
            try:
                _propose_one(row, already, asked, by_native, proposed, refused,
                             events_path=events_path, source_skill=source_skill)
            except Exception as exc:  # noqa: BLE001
                if close_mode != "propose":
                    raise
                refused.append({"commitment_id": str(row.get("commitment_id")),
                                "why": type(exc).__name__, "close_mode": True})

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


def is_chat_proposal(event: Optional[dict]) -> bool:
    """True for a review proposal this leg wrote (cr1#164), the sibling of
    `is_chat_evidenced`: keyed on what survives a dropped pointer. Newer rows
    carry `proposed_by`. A row from before that field is known by either half
    of its pointer, or by `source_skill` when the merged fire wrote it (the
    fire passes `reconcile-chat`).

    THE ONE ROW THIS CANNOT SEE: a pre-field row written with the leg's default
    `source_skill` (`reconcile-sent`, `SOURCE_SKILL`) or End of Day's
    (`past-meetings`) that has lost BOTH halves of its pointer. The mail rail
    writes those same words, so nothing on the row says chat, and claiming
    otherwise would flag mail proposals.

    A proposal the person confirmed closes under their own word, so that
    close carries a session pointer and this row stays the place its chat
    pointer lives."""
    if not isinstance(event, dict) or event.get("type") != "commitment_review_proposed":
        return False
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return (str(data.get("proposed_by") or "").strip() == PROPOSED_BY
            or str(event.get("source_skill") or "").strip() == JOB_ID
            or data.get("chat_source_ref") is not None
            or _chat.is_chat_ref_string(data.get("source_ref")))


# ---------------------------------------------------------------------------
# Receipt + validator
# ---------------------------------------------------------------------------

def _summary(receipt: dict) -> str:
    """The line a surface may paste verbatim. Plain language, no field names,
    and it NEVER claims coverage the scan did not have."""
    n_scanned = receipt.get("n_scanned") or 0
    n_closed = receipt.get("n_closed") or 0
    n_prop = receipt.get("n_proposed") or 0
    n_excluded = receipt.get("n_excluded") or 0
    n_checked = n_scanned - n_excluded
    if n_scanned == 0:
        line = "No new chat since the last check — nothing to reconcile."
    elif n_checked <= 0:
        # cr1#770: chat was read, so never "no new chat"; the counts follow.
        line = (f"Read {n_scanned} chat message{'s' if n_scanned != 1 else ''}, "
                "none of them checked.")
    elif n_closed == 0 and n_prop == 0:
        line = (f"Checked {n_checked} chat message"
                f"{'s' if n_checked != 1 else ''} — nothing matched an open "
                "commitment.")
    else:
        bits = []
        if n_closed:
            bits.append(f"closed {n_closed} you'd already handled")
        if n_prop:
            bits.append(f"{n_prop} to confirm")
        line = "Reconciled your chat: " + ", ".join(bits) + "."
    if n_excluded:
        line += f" Your include or exclude list set aside {n_excluded}."
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
    if receipt.get("n_close_mode_refused"):
        n = receipt["n_close_mode_refused"]
        line += (f" {n} match{'es' if n != 1 else ''} could not go on your "
                 "confirm list and will be tried again on a new message.")
    if receipt.get("coverage_note"):
        line += " " + receipt["coverage_note"]
    if receipt.get("read_partial"):
        # cr1#742: true because the cursor held (the cursor block above)
        line += (" The chat had more than this pass read; the same span is "
                 "read again on the next pass.")
    return line


_RECEIPT_KEYS = ("provider", "scan_mode", "degraded", "coverage_note",
                 "scan_limits", "cursor_before", "cursor_after",
                 "skip_reason", "blocked_reason",
                 "n_deferred", "deferred_from_ts",
                 "cursor_advanced_over_partial_sweep", "read_partial",
                 "n_scanned", "n_excluded", "n_dropped_noise", "n_dropped_bad_ts",
                 "n_dropped_direction",
                 "n_candidates", "n_candidates_over_cap", "n_closed",
                 "n_proposed", "n_refused_no_pointer",
                 "close_mode", "n_close_mode_proposed", "n_close_mode_refused",
                 MAINTENANCE_WINDOW_FIELD)  # cr1#663


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
