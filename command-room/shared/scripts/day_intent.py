#!/usr/bin/env python3
"""
day_intent — THE typed record of what a day is about (SPEC BK1, Daily Bookends).

WHY THIS EXISTS
===============
"Tomorrow is about closing Acme Co" is the single most load-bearing sentence a
CEO says at the end of a day, and before this module it had nowhere to live. It
went into prose — a session note, a wrap paragraph, a chat line — where the
morning surface could only find it by grepping, which means every surface
invents its own answer and they disagree. A tiny typed record with ONE reader
is the whole fix.

DOCTRINE (mirrored from the objective / commitment lanes)
--------------------------------------------------------
  - **Append-only, latest-wins.** A day's intent is never edited. Changing your
    mind appends a NEW `day_intent` for the same `for_date`; the LATEST event
    wins (the OBJ2 supersession posture — history is the record of what you
    thought when, and nothing rewrites it).
  - **ONE reader.** `load_day_intent(workspace_root, for_date)` is the only way
    a surface learns what a day is about. Nobody greps. A surface that wants
    the pre-confirm draft asks for it explicitly (`include_proposed=True`) — it
    can never arrive by accident.
  - **A guess never masquerades as the CEO's word.** `origin="proposed"` is an
    auto-draft awaiting a tap. The default read SKIPS proposed rows outright,
    so no surface can render one as fact by forgetting to check a flag; every
    returned record additionally carries `stated` (True for `wrap`/`manual`).
  - **The date is workspace-LOCAL, never UTC.** `for_date` resolves through
    `tz.py`. At 9pm Pacific the UTC calendar has already rolled over, so a
    UTC-derived "tomorrow" is the day AFTER the one the CEO means — the exact
    hour the end-of-day chat fires. Unresolvable TZ raises rather than
    degrading: the date IS the record's identity, so a wrong one is worse than
    a refusal.
  - **NO hand-rolled writes.** Every append routes through
    `event_gate.append_event` (schema enum, seq/ts stamping, the writer lock).
  - **Born with provenance (SPEC PROV1).** Every write takes a `source_ref`,
    canonicalized through Layer A4 (`connector_adapters.provenance`). A manual
    chat write's pointer is its session/receipt id (`session:<id>`); a write
    with nothing to point at lands marked `provenance_missing`, never silently
    pointerless.
  - **Reversible.** Every write is stamped with a `brain_batch_id` +
    `brain_change_class="day_intent"`, so `brain_undo.undo_batch` can reverse
    it through the registered reverser. Reversal is ADDITIVE: it appends the
    restoring record (or a retraction), never edits history.

WRITERS (SPEC BK1 §Writers)
---------------------------
  1. `origin="manual"` — the natural-language path through workspace-manager
     ("tomorrow is about X"). SHIPPED in BK1.
  2. `origin="wrap"` — the end-of-day chat's one-tap confirm. EOD1 calls
     `write_day_intent(..., origin="wrap")`; no wrap surface exists yet, and
     BK1 wires none.
  3. `origin="proposed"` — the end-of-day chat's auto-draft, written ONLY on
     tap-confirm, never silently. Also EOD1's caller.

The API is deliberately origin-agnostic so legs 2 and 3 need no change here.

stdlib only.
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


class DayIntentError(ValueError):
    """A day-intent write or date resolution was refused. Fail loud — a silent
    fallthrough here writes a record under the wrong day, which reads as a
    perfectly plausible intent for a day the CEO never spoke about."""


# The three sanctioned origins (SPEC BK1). `manual` is the chat path shipped in
# BK1; `wrap` and `proposed` are the end-of-day chat's two legs (EOD1).
ORIGINS = ("wrap", "manual", "proposed")

# The origins that carry the CEO's OWN word. A `proposed` row is a draft the
# system guessed; it is never a stated intent, and the default read never
# returns one.
STATED_ORIGINS = ("wrap", "manual")

# A day is about at most three things. The cap is the point of the record: a
# list of eight is a to-do list, and the CEO already has one.
MAX_ITEMS = 3

# The change class `brain_undo.REVERSERS` registers for this lane.
CHANGE_CLASS = "day_intent"

# The event type. One spelling, one home.
EVENT_TYPE = "day_intent"

# Item text budget. Clipped (never refused) at the writer — a CEO mid-sentence
# should not get an error dialog.
ITEM_TEXT_MAX_CHARS = 200

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The relative day words the writer accepts. Anything else must be an explicit
# ISO date — "next Tuesday" is a parse this module deliberately does not own.
_RELATIVE_DAYS = {"today": 0, "tomorrow": 1, "yesterday": -1}


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


# ---------------------------------------------------------------------------
# Dates — workspace-local, never UTC
# ---------------------------------------------------------------------------

def workspace_today(workspace_root, *, now=None) -> datetime.date:
    """The workspace's OWN calendar date right now (SPEC BK1 §TZ discipline).

    `now` is an aware datetime (or any timestamp `tz.to_local` accepts) —
    tests pass an explicit instant; production passes nothing and gets the
    real clock. Naive input is read as UTC, exactly as `tz.to_local` documents.

    Raises `DayIntentError` when the workspace TZ cannot be resolved. The
    silent-UTC branch is what tz.py was rewritten in v3.11.1 to remove, and a
    day_intent under the wrong date is that same bug with a longer fuse: it
    reads as a perfectly plausible intent for the wrong day.
    """
    from tz import TZResolutionError, to_local

    value = now if now is not None else datetime.datetime.now(datetime.timezone.utc)
    try:
        local = to_local(value, workspace_path=workspace_root)
    except TZResolutionError as exc:
        raise DayIntentError(
            "day_intent needs the workspace timezone to know which day "
            f"'today' is, and it could not be resolved ({exc}). Set it with "
            "'set my timezone to <name>' — a UTC guess would file the intent "
            "under the wrong day every evening."
        ) from exc
    if local is None:  # pragma: no cover — to_local only returns None for None
        raise DayIntentError("day_intent could not resolve a local time from "
                             f"{now!r}")
    return local.date()


def resolve_for_date(workspace_root, value: Any = None, *, now=None) -> str:
    """The `for_date` a write or read governs, as workspace-local `YYYY-MM-DD`.

    Accepts:
      - `None` → TOMORROW (the default: this record exists to answer "what is
        tomorrow about", and the end-of-day chat is its home);
      - `"today"` / `"tomorrow"` / `"yesterday"` (case/space-insensitive);
      - a `datetime.date` / `datetime.datetime` → its calendar date;
      - an explicit `"YYYY-MM-DD"` string → validated and returned verbatim.

    The relative words resolve through `workspace_today` (tz.py). An explicit
    ISO date needs no clock at all, so a reader passing one never touches the
    TZ layer — that is why `load_day_intent` can answer for a date on a
    workspace whose timezone is unset.
    """
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if value is None:
        return (workspace_today(workspace_root, now=now)
                + datetime.timedelta(days=1)).isoformat()
    if not isinstance(value, str):
        raise DayIntentError(
            f"for_date must be a date, an ISO 'YYYY-MM-DD' string, or one of "
            f"{sorted(_RELATIVE_DAYS)} — got {type(value).__name__}")
    token = value.strip().lower()
    if not token:
        raise DayIntentError("for_date is empty — pass a date or 'tomorrow'")
    if token in _RELATIVE_DAYS:
        return (workspace_today(workspace_root, now=now)
                + datetime.timedelta(days=_RELATIVE_DAYS[token])).isoformat()
    raw = value.strip()
    if not _DATE_RE.match(raw):
        raise DayIntentError(
            f"for_date {value!r} is not a workspace-local 'YYYY-MM-DD' date "
            f"or one of {sorted(_RELATIVE_DAYS)}. Phrases like 'next Tuesday' "
            "are resolved by the caller, not here — this module will not "
            "guess a calendar.")
    try:
        datetime.date.fromisoformat(raw)
    except ValueError as exc:
        raise DayIntentError(
            f"for_date {value!r} is not a real calendar date ({exc})") from exc
    return raw


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def normalize_items(items) -> list:
    """Validate + normalize the 1–3 item list into the stored shape.

    Each item is `{text, rank}` plus an optional `commitment_id`. A plain
    string is accepted as shorthand for `{"text": ...}` — the chat path builds
    them from a spoken sentence and should not have to construct dicts.

    `rank` is the CEO's ordering, 1-based. Absent ranks are assigned in list
    order. A partially-ranked list is refused: half an ordering is not one, and
    silently filling the gaps invents a priority nobody stated.
    """
    from text_clip import clip

    if items is None or (isinstance(items, str) and not items.strip()):
        seq = []
    elif isinstance(items, (str, bytes, dict)):
        seq = [items]
    elif isinstance(items, (list, tuple)):
        seq = list(items)
    else:
        raise DayIntentError(
            f"items must be a list of 1–{MAX_ITEMS} entries, got "
            f"{type(items).__name__}")

    if not seq:
        raise DayIntentError(
            "a day_intent needs at least one item — an empty intent is not a "
            "statement about the day. To reverse an intent, use "
            "brain_undo.undo_batch (or reverse_day_intent); do not write an "
            "empty one.")
    if len(seq) > MAX_ITEMS:
        raise DayIntentError(
            f"a day_intent carries at most {MAX_ITEMS} items — got "
            f"{len(seq)}. More than three is a to-do list, and the CEO "
            "already has one; the cap is the point of this record.")

    out: list = []
    explicit_ranks: list = []
    for i, raw in enumerate(seq):
        if isinstance(raw, str):
            row: dict = {"text": raw}
        elif isinstance(raw, dict):
            row = dict(raw)
        else:
            raise DayIntentError(
                f"item {i + 1} must be a string or an object with 'text', got "
                f"{type(raw).__name__}")
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            raise DayIntentError(
                f"item {i + 1} has no text — an intent item is the sentence "
                "the CEO said, and there is nothing else to render")
        item: dict = {"text": clip(text.strip(), ITEM_TEXT_MAX_CHARS)}
        cid = row.get("commitment_id")
        if cid is not None:
            if not isinstance(cid, str) or not cid.strip():
                raise DayIntentError(
                    f"item {i + 1} commitment_id must be a non-empty string "
                    f"(the commitment's data.id), got {cid!r}")
            item["commitment_id"] = cid.strip()
        rank = row.get("rank")
        if rank is not None:
            try:
                rank = int(rank)
            except (TypeError, ValueError) as exc:
                raise DayIntentError(
                    f"item {i + 1} rank must be an integer, got "
                    f"{row.get('rank')!r}") from exc
            explicit_ranks.append(rank)
            item["rank"] = rank
        out.append(item)

    if explicit_ranks:
        if len(explicit_ranks) != len(out):
            raise DayIntentError(
                "rank is set on some items and not others — half an ordering "
                "is not an ordering, and filling the gaps would invent a "
                "priority nobody stated. Rank all of them or none.")
        if sorted(explicit_ranks) != list(range(1, len(out) + 1)):
            raise DayIntentError(
                f"ranks must be 1..{len(out)} with no gaps or duplicates, got "
                f"{explicit_ranks}")
    else:
        for i, item in enumerate(out, start=1):
            item["rank"] = i
    return out


# ---------------------------------------------------------------------------
# Reader — THE door. Nobody greps.
# ---------------------------------------------------------------------------

def _day_intent_rows(workspace_root, for_date: str) -> list:
    """Every well-formed `day_intent` event for `for_date`, in append order.

    Reads through `event_refs.load_events`, the defensive loader: a malformed
    line in events.jsonl is SKIPPED, never fatal. A morning surface that
    crashes because one row is half-written is worse than one that says
    nothing.
    """
    from event_refs import load_events
    from event_seq import event_seq

    rows = []
    for order, ev in enumerate(load_events(_events_path(workspace_root))):
        if not isinstance(ev, dict) or ev.get("type") != EVENT_TYPE:
            continue
        data = ev.get("data")
        if not isinstance(data, dict):
            continue
        if data.get("for_date") != for_date:
            continue
        rows.append((event_seq(ev), order, ev))
    # Append order is the tiebreak, so a seq-less row (a hand-written or
    # pre-gate line) still has a defined position instead of vanishing.
    rows.sort(key=lambda r: (r[0] if r[0] is not None else -1, r[1]))
    return [ev for _, _, ev in rows]


def _governs(ev: dict) -> bool:
    """True when this row is one the DEFAULT read considers — a stated record
    (`wrap`/`manual`) or a retraction.

    ONE definition, two callers: the reader's default filter and the
    reverser's "what came before this" walk. Two spellings of "the row that
    governs the day" would drift, and the way they would drift is a reversal
    restoring an unconfirmed `proposed` draft as the CEO's answer."""
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    return bool(data.get("origin") in STATED_ORIGINS or data.get("retracted"))


def _record_from_event(ev: dict) -> dict:
    """The stored event → the record every surface renders."""
    from event_seq import event_seq
    from event_time import event_time

    data = ev.get("data") or {}
    origin = data.get("origin")
    items = data.get("items")
    return {
        "for_date": data.get("for_date"),
        "items": [i for i in (items or []) if isinstance(i, dict)],
        "origin": origin,
        "stated": origin in STATED_ORIGINS,
        "seq": event_seq(ev),
        "ts": event_time(ev),
        "source_ref": data.get("source_ref"),
        "source_skill": ev.get("source_skill"),
        "retracted": bool(data.get("retracted")),
    }


def load_day_intent(workspace_root, for_date, *,
                    include_proposed: bool = False) -> Optional[dict]:
    """THE reader (SPEC BK1). The winning `day_intent` record for `for_date`,
    or None.

    "Winning" is LATEST-WINS over append order — the OBJ2 supersession posture.
    The CEO changing their mind appends a new record; this returns the last
    one, and every earlier one stays in history untouched.

    Returns None when: no record exists for that day; the winner is a
    RETRACTION (an undo with nothing to restore); or — by default — the only
    records are `proposed` drafts.

    `include_proposed=False` (the default) is the fence behind SPEC BK1's
    "no surface renders a proposed intent as fact". A `proposed` row is a guess
    the system made, and it never displaces or stands in for the CEO's own
    word. A surface that deliberately wants the pre-confirm draft — the
    end-of-day chat rendering "here's what I think tomorrow is about, confirm?"
    — passes `include_proposed=True` and reads the returned `stated` flag.

    `for_date` accepts anything `resolve_for_date` does; an explicit
    'YYYY-MM-DD' needs no clock, so this answers on a workspace with no
    timezone set.
    """
    target = resolve_for_date(workspace_root, for_date)
    rows = _day_intent_rows(workspace_root, target)
    if not include_proposed:
        rows = [ev for ev in rows if _governs(ev)]
    if not rows:
        return None
    record = _record_from_event(rows[-1])
    if record["retracted"]:
        return None
    if record["origin"] not in ORIGINS:
        # A row whose origin is unreadable is not a statement about the day.
        # Skip it rather than render an intent of unknown provenance.
        return None
    if not record["items"]:
        return None
    return record


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

# The undo-batch prefix + salt width, mirroring `commitment_backlog_sweep`'s
# `swb_` mint. A day_intent write is HUMAN-TRIGGERED — "tomorrow is about X",
# then a correction ten seconds later — so a to-the-second stamp is not enough:
# two writes sharing a batch id means ONE `undo` reverses BOTH, including the
# one the CEO was happy with. The batch id IS the undo contract, so it cannot
# be almost-unique.
BATCH_PREFIX = "di_"
BATCH_SALT_BYTES = 4


def _batch_id(now=None) -> str:
    """`di_<UTC to the second>-<8 hex>` — sortable, readable aloud, unique per
    gesture rather than per second (the `swb_` precedent, review F-5/F-9)."""
    import secrets

    stamp = now if isinstance(now, datetime.datetime) else \
        datetime.datetime.now(datetime.timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return (f"{BATCH_PREFIX}"
            f"{stamp.astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            f"-{secrets.token_hex(BATCH_SALT_BYTES)}")


def write_day_intent(workspace_root, items, *, origin: str,
                     for_date: Any = None,
                     source_ref: Any = None,
                     source_skill: str = "workspace-manager",
                     batch_id: Optional[str] = None,
                     now=None) -> dict:
    """THE day-intent writer. Appends ONE `day_intent` event and returns
    `{status, for_date, seq, batch_ref, record, event}`.

    `origin` is one of `ORIGINS`. BK1 ships the `manual` caller (the chat
    path); `wrap` and `proposed` are EOD1's, and this signature is what lets
    EOD1 land without touching this module. `proposed` is legal here because
    the end-of-day chat writes it on TAP-CONFIRM — never silently — and the
    default reader refuses to hand it back as fact regardless.

    `for_date` defaults to TOMORROW, resolved workspace-local through tz.py
    (`resolve_for_date`). Never UTC.

    `source_ref` (SPEC PROV1) is the pointer to what justified this write,
    canonicalized through Layer A4. A chat write passes its session/receipt id
    (`session:<id>`); an absent pointer lands `provenance_missing: true` — the
    write is never blocked — and a MALFORMED one raises `SourceRefError`
    before anything lands, because a garbage ref reads downstream as
    resolvable evidence.

    `batch_id` is the undo handle. One is minted when the caller passes none,
    so EVERY day_intent write is reversible by construction
    (`brain_undo.undo_batch(ws, result["batch_ref"], ...)`). A caller folding
    this write into a larger gesture passes its own batch id and the whole
    gesture reverses together.
    """
    from connector_adapters.provenance import close_provenance_fields
    from event_gate import append_event

    if origin not in ORIGINS:
        raise DayIntentError(
            f"origin must be one of {list(ORIGINS)}, got {origin!r} — the "
            "origin is how every surface tells the CEO's own word from a "
            "draft the system guessed")
    if not isinstance(source_skill, str) or not source_skill.strip():
        raise DayIntentError("write_day_intent needs a source_skill")

    target = resolve_for_date(workspace_root, for_date, now=now)
    normalized = normalize_items(items)
    bid = str(batch_id).strip() if batch_id else _batch_id(now)

    data: dict = {
        "for_date": target,
        "items": normalized,
        "origin": origin,
        # The undo handle. The two stamps travel TOGETHER — a batch id with no
        # change class is a row `brain_undo._changes_for_brain_batch` silently
        # skips, i.e. an undo handle that looks present and reverses nothing.
        "brain_batch_id": bid,
        "brain_change_class": CHANGE_CLASS,
    }
    # PROV1 — refuses a malformed pointer, marks an absent one, never blocks.
    data.update(close_provenance_fields(source_ref))

    written = append_event(_events_path(workspace_root), [{
        "type": EVENT_TYPE,
        "source_skill": source_skill,
        "data": data,
    }], holder=f"day_intent:{source_skill}")
    ev = written[0]
    return {
        "status": "written",
        "for_date": target,
        "seq": ev.get("seq"),
        "batch_ref": {"kind": "brain_batch", "batch_id": bid},
        "record": _record_from_event(ev),
        "event": ev,
    }


def write_from_proposal(workspace_root, proposal, *, origin: str = "wrap",
                        source_ref: Any = None,
                        source_skill: str = "past-meetings",
                        batch_id: Optional[str] = None,
                        now=None) -> dict:
    """Write the end-of-day tomorrow tap's CONFIRMED proposal, whole.

    Takes `end_of_day.resolve_intent_confirm(...)["proposal"]` verbatim and
    hands its `items` — the DICTS, not their texts — to `write_day_intent`.

    WHY THIS EXISTS (SPEC EODFIX1 §1-4). The proposal the End of Day puts on
    screen carries, per item, the commitment it was drafted from. The written
    intent carried text and rank only, so the morning brief could not join
    tomorrow's stated intent back to the open book — and the cause was not a
    caller being careless, it was the fire's own text INSTRUCTING the strip:
    `write_day_intent(ws, [i["text"] for i in res["proposal"]["items"]], ...)`.
    A list comprehension in prose is a data contract nobody owns. This function
    is that contract, in code, so the ids cannot be dropped by a caller
    following instructions.

    `origin` defaults to `wrap` because a tap is the CEO's own word — the
    record is STATED from the moment it is confirmed. `for_date` is the
    PROPOSAL's, never re-derived: the draft was for a specific day, and
    resolving "tomorrow" again at write time files it under the wrong one for
    a tap that lands either side of midnight.

    An item carrying an EMPTY `commitment_id` (the proposal builder emits one
    for a rollover row that had no id) has the key dropped rather than the
    write refused: an empty id is an absent id, and refusing the whole confirm
    over it would lose the CEO's tap.
    """
    if not isinstance(proposal, dict):
        raise DayIntentError(
            "write_from_proposal takes the proposal dict "
            "`end_of_day.resolve_intent_confirm` returned, got "
            f"{type(proposal).__name__} — a caller re-assembling one by hand is "
            "the id-dropping bug this function exists to close")
    raw_items = proposal.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise DayIntentError(
            "the proposal carries no items — there is nothing to confirm. "
            "`resolve_intent_confirm` already refuses that case; a caller "
            "reaching here with it has lost the proposal, not gained an empty "
            "intent")

    items: list = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            # A bare string still writes — the ids are the point, but a
            # confirm must never be lost over a shape.
            items.append(raw)
            continue
        row = {k: v for k, v in raw.items()
               if k in ("text", "rank", "commitment_id")}
        cid = row.get("commitment_id")
        if not (isinstance(cid, str) and cid.strip()):
            row.pop("commitment_id", None)
        items.append(row)

    return write_day_intent(workspace_root, items, origin=origin,
                            for_date=proposal.get("for_date"),
                            source_ref=source_ref,
                            source_skill=source_skill,
                            batch_id=batch_id, now=now)


# ---------------------------------------------------------------------------
# Reversal — additive, never an edit
# ---------------------------------------------------------------------------

def reverse_day_intent(workspace_root, seq, *, undone_by: str,
                       source_skill: str = "workspace-manager") -> dict:
    """Reverse ONE day_intent write (the `brain_undo` registry's reverser).

    ADDITIVE, like every reverser in the registry: the reversed event stays in
    history forever. What lands is a NEW `day_intent` for the same `for_date`
    that either

      - RESTORES the record this one superseded (`data.restores_seq`), or
      - RETRACTS the day (`data.retracted: true`, `items: []`) when there was
        nothing before it — `load_day_intent` reads a retraction as None.

    A retraction is the ONLY legal empty-items record, which is why the writer
    refuses an empty list: "no intent" is a reversal, never a statement.

    WHAT "PREVIOUS" MEANS: the record the READER would have shown before this
    write — the nearest earlier row that GOVERNS (a stated record, or a
    retraction). A `proposed` draft sitting in between is skipped, because
    restoring one would make an unconfirmed guess the day's answer, which is
    the single thing SPEC BK1's proposed rules exist to prevent.

    When a LATER day_intent already superseded the target, nothing is written
    and the status is `already_superseded` — the newer record already governs
    the day, and restoring an older one over it would reverse a change the
    user never asked to reverse. "Superseded" is read the same way: a later
    GOVERNING row supersedes anything, and a later row of any kind supersedes
    a `proposed` target (two drafts for one day: the newer is the draft).
    """
    from event_gate import append_event
    from event_seq import coerce_seq

    target_seq = coerce_seq(seq, context="day_intent seq")
    if target_seq is None:
        raise DayIntentError(
            f"day_intent reversal needs the seq of the event to reverse, got "
            f"{seq!r}")

    from event_refs import load_events
    from event_seq import event_seq as _seq_of

    target = None
    for ev in load_events(_events_path(workspace_root)):
        if isinstance(ev, dict) and ev.get("type") == EVENT_TYPE \
                and _seq_of(ev) == target_seq:
            target = ev
            break
    if target is None:
        raise DayIntentError(
            f"no day_intent event at seq {target_seq} — nothing to reverse")

    for_date = (target.get("data") or {}).get("for_date")
    if not isinstance(for_date, str) or not for_date:
        raise DayIntentError(
            f"the day_intent at seq {target_seq} carries no for_date — it is "
            "malformed and cannot be anchored")

    rows = _day_intent_rows(workspace_root, for_date)
    positions = [i for i, ev in enumerate(rows) if _seq_of(ev) == target_seq]
    if not positions:  # pragma: no cover — the row was just found by seq
        raise DayIntentError(
            f"the day_intent at seq {target_seq} is not among {for_date}'s "
            "records")
    idx = positions[-1]
    target_proposed = (target.get("data") or {}).get("origin") \
        not in STATED_ORIGINS
    later = [ev for ev in rows[idx + 1:]
             if target_proposed or _governs(ev)]
    if later:
        return {"status": "already_superseded", "for_date": for_date,
                "seq": target_seq,
                "superseded_by": _seq_of(later[-1])}

    prior = next((ev for ev in reversed(rows[:idx]) if _governs(ev)), None)
    prior_data = (prior.get("data") or {}) if prior else {}
    data: dict = {
        "for_date": for_date,
        "reverses_seq": target_seq,
        "reversed_by": undone_by,
        # Facts about a reversal are sourced like every other write (the
        # `entity_fact_retracted` precedent): a synthesized pointer, never null.
        "source_ref": f"undo:{source_skill}:seq:{target_seq}",
    }
    if prior is not None and not prior_data.get("retracted") \
            and prior_data.get("items"):
        data["items"] = prior_data.get("items")
        data["origin"] = prior_data.get("origin")
        data["restores_seq"] = _seq_of(prior)
        status = "restored"
    else:
        data["items"] = []
        data["origin"] = (target.get("data") or {}).get("origin")
        data["retracted"] = True
        status = "retracted"

    # Deliberately NOT stamped with brain_batch_id / brain_change_class: a
    # reversal is not itself an undoable change class, and stamping it would
    # put the undo of an undo on the bare-`undo` listing as a second batch.
    written = append_event(_events_path(workspace_root), [{
        "type": EVENT_TYPE,
        "source_skill": source_skill,
        "data": data,
    }], holder=f"day_intent_undo:{source_skill}")
    return {"status": status, "for_date": for_date, "seq": target_seq,
            "reversal_seq": written[0].get("seq"),
            "restores_seq": data.get("restores_seq"),
            "event": written[0]}


__all__ = [
    "CHANGE_CLASS",
    "EVENT_TYPE",
    "ITEM_TEXT_MAX_CHARS",
    "MAX_ITEMS",
    "ORIGINS",
    "STATED_ORIGINS",
    "DayIntentError",
    "load_day_intent",
    "normalize_items",
    "resolve_for_date",
    "reverse_day_intent",
    "workspace_today",
    "write_day_intent",
    "write_from_proposal",
]
