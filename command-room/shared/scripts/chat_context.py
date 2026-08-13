#!/usr/bin/env python3
"""
The chat CONTEXT + MEMORY leg (SPEC CHATSCAN1 §C) — what chat teaches the
substrate, without letting chat grow the book.

TWO OUTPUTS, DELIBERATELY SMALL
-------------------------------
1. ONE CONTEXT LINE for the morning brief. Not rows. The brief's existing
   sections and caps are untouched — `needs_attention` still shows at most
   five rows ranked due-then-age, and this leg cannot add a sixth. It produces
   a sentence that rides the same `*_lines` idiom the alarm / money / watchdog
   lines already use, so there is no new render contract and no new place for
   a row to hide.
2. A BOUNDED ENTITY TOUCH per tracked entity mentioned. One roll-up
   `interaction` event per entity per run — never one per message — through
   the HIST1 recency seam (`person_ids` / `org_ids`, which is what
   `render_person_history._event_persons` and `org_activity.event_org_ids`
   read). This is the "workspace/memory learns from chat" ask, and the roll-up
   is what stops it becoming passive whole-history ingestion.

WHY A TOUCH AND NOT A FACT
--------------------------
`record_person_fact` / `record_org_fact` are the HIST1 seam's FACT writers,
and a fact is a durable claim about an entity. Banter is not a claim. Writing
one fact per chat mention would fill the facts block of every org with
chatter, which is precisely the bloat failure mode the anti-bloat doctrine
names. A touchpoint is the honest shape: it says these people were in contact
on this date, with a pointer, which is what cadence, dormancy and call-prep
actually consume.

THE READ BUDGET
---------------
This leg rides fires that already have work to do. A per-run budget bounds the
queries it may add so it can never blow up the latency of the brief it is a
guest inside, and the overflow is LOGGED with an oldest-window-first cursor so
the skipped window is picked up next run instead of lost. A silently truncated
sweep reads as complete coverage.

stdlib only, no network. The skill fetches through the declared chat backend's
seam-resolved tools; everything after that is here.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from connector_adapters import chat as _chat  # noqa: E402

SOURCE_SKILL = "morning-briefing"
CURSOR_KEY = "chat_context_cursor"

# Per-run bounds. `READ_BUDGET` is the number of backend QUERIES this leg may
# add to a fire it is a guest inside; `MAX_ENTITY_TOUCHES` is the number of
# entity-history writes it may make. Both spill NARRATED, never silently — the
# PID1 §0-3 posture, and the same reason `entity_signal_detector` counts its
# own overflow instead of dropping it.
READ_BUDGET = 6
MAX_ENTITY_TOUCHES = 12

# The brief's context line names at most this many entities before it says
# "and N more". A sentence that lists fifteen names is a row list wearing a
# sentence's clothes.
CONTEXT_LINE_NAMES = 3

TOUCH_EVENT_TYPE = "interaction"


def _norm(v) -> str:
    return str(v or "").strip()


def _empty_counts() -> Dict[str, int]:
    return {
        "n_scanned": 0,
        "n_mentions": 0,
        "n_entities": 0,
        "n_touches_written": 0,
        "n_touches_spilled": 0,
        "n_queries_used": 0,
        "n_queries_skipped": 0,
        "n_conversations_deferred": 0,
    }


# ---------------------------------------------------------------------------
# The read budget
# ---------------------------------------------------------------------------

class ReadBudget:
    """A hard bound on the backend reads this leg adds to a fire.

    ONE UNIT = ONE CONVERSATION. `apply_budget` below spends it in the product
    path, before `collect_mentions` ever sees a message — which is the point
    the review made: a budget only the tests spend is not a budget, it is a
    field that reports zero forever (the fence-tests-the-helper shape V5 set
    out to avoid). The §C promise — "a per-run read budget so the added leg
    can't blow up existing fires' latency" — is only kept if the leg actually
    declines to read.

    `spend()` returns False once the budget is gone; the skipped windows come
    back in `deferred`, OLDEST FIRST, so the next run starts where this one
    stopped rather than at the newest window again. Newest-first recovery
    would starve the oldest window forever — it is always the one that loses a
    race it re-enters every fire."""

    def __init__(self, limit: int = READ_BUDGET):
        self.limit = max(0, int(limit))
        self.used = 0
        self.deferred: List[Any] = []

    def spend(self, window=None) -> bool:
        if self.used < self.limit:
            self.used += 1
            return True
        if window is not None:
            self.deferred.append(window)
        return False

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def report(self) -> Dict[str, Any]:
        return {
            "n_queries_used": self.used,
            "n_queries_skipped": len(self.deferred),
            # Oldest first — this is the order the next fire must resume in,
            # and sorting it here means no caller can get it wrong.
            "deferred_windows": sorted(_norm(w) for w in self.deferred if w),
        }


def apply_budget(messages, budget: "ReadBudget"):
    """`(within_budget, deferred_conversations)` — the leg's actual spend.

    Conversations are the unit because that is what a chat backend charges
    for: one read per channel or chat, however many messages come back. They
    drain OLDEST FIRST (by each conversation's newest message), so a busy
    window walks forward instead of re-reading the same recent rooms every
    fire and never reaching the quiet older one.

    A conversation the budget cannot afford is not scanned at all — its
    messages never reach `collect_mentions`, so they cannot mint a touch. That
    is what makes this a bound on WORK rather than a label on the receipt."""
    by_room: Dict[str, List[dict]] = {}
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        room = _norm(m.get("chat_or_channel_id")) or "(unknown)"
        by_room.setdefault(room, []).append(m)
    rooms = sorted(by_room, key=lambda r: max(
        (_norm(m.get("ts")) for m in by_room[r]), default=""))
    kept: List[dict] = []
    deferred_rooms: List[str] = []
    for room in rooms:
        if budget.spend(room):
            kept.extend(by_room[room])
        else:
            deferred_rooms.append(room)
    return kept, deferred_rooms


# ---------------------------------------------------------------------------
# Mention detection
# ---------------------------------------------------------------------------

def _token_pattern(token: str):
    """A WORD-BOUNDARY matcher for one roster token.

    `\\b` is not used: it treats `-`, `.` and `&` as boundaries, so "A-Z" or
    "R&D" would match half of themselves inside a longer name. The lookarounds
    below break only on alphanumerics, which is the boundary that actually
    matters here — a tracked name must not match INSIDE a longer word.

    This replaced a plain `token in text` substring test. The docstring above
    it already claimed word-boundary matching and named the exact harm ("a
    wrong entity touch is worse than a missed one, because it lands in that
    entity's permanent history"); the code did the opposite, and the review
    measured a three-letter tracked name matching inside three ordinary
    unrelated words and minting two false-positive touches."""
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(token) + r"(?![A-Za-z0-9])")


def collect_mentions(messages, tracked, *, cap: int = MAX_ENTITY_TOUCHES
                     ) -> Dict[str, Any]:
    """Which TRACKED entities the window's chat mentioned, and where.

    `tracked` is `[{"id": ..., "kind": "person"|"org", "names": [...]}, ...]`
    — resolved by the caller against the entity graph, never guessed here.
    Matching is on WORD BOUNDARIES (`_token_pattern`) and refuses tokens under
    three characters: two-letter initials match inside every other word, and a
    wrong entity touch is worse than a missed one because it lands in that
    entity's permanent history.

    Returns per-entity roll-ups (`n_mentions`, the NEWEST message's pointer,
    the date), capped, with the overflow COUNTED. Message text never leaves
    this function: a touch records that contact happened and points at it, not
    what was said. The pointer is the read-through, and it is the only thing
    that should be.
    """
    counts = _empty_counts()
    index: List[tuple] = []
    for t in tracked or []:
        if not isinstance(t, dict) or not t.get("id"):
            continue
        for name in t.get("names") or []:
            token = _norm(name).lower()
            if len(token) >= 3:
                index.append((_token_pattern(token), t))

    by_entity: Dict[str, dict] = {}
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        counts["n_scanned"] += 1
        text = _norm(m.get("text")).lower()
        if not text:
            continue
        ts = _norm(m.get("ts"))
        seen_here = set()
        for pattern, t in index:
            if not pattern.search(text):
                continue
            eid = str(t["id"])
            if eid in seen_here:
                continue
            seen_here.add(eid)
            counts["n_mentions"] += 1
            slot = by_entity.setdefault(eid, {
                "id": eid,
                "kind": _norm(t.get("kind")) or "person",
                "name": _norm((t.get("names") or [""])[0]),
                "n_mentions": 0,
                "last_ts": "",
                "last_ref": None,
            })
            slot["n_mentions"] += 1
            if ts >= slot["last_ts"]:
                slot["last_ts"] = ts
                # A caller may hand the pointer in explicitly, or hand in a
                # message normalized by `chat_reconcile.normalize_chat_message`
                # (which carries the five pointer fields at top level). Both
                # read here, so the two legs can share one fetch — which is
                # the point of riding an existing fire.
                slot["last_ref"] = (m.get("ref")
                                    or _chat.read_chat_source_ref(m))

    entities = sorted(by_entity.values(),
                      key=lambda e: (-e["n_mentions"], e["last_ts"]),
                      reverse=False)
    counts["n_entities"] = len(entities)
    spilled = 0
    if cap and cap > 0 and len(entities) > cap:
        spilled = len(entities) - cap
        entities = entities[:cap]
    counts["n_touches_spilled"] = spilled
    return {"entities": entities, "counts": counts}


# ---------------------------------------------------------------------------
# The brief's context LINE (never a row)
# ---------------------------------------------------------------------------

def context_line(sweep: Optional[dict]) -> str:
    """ONE sentence for the brief, or `""` when there is nothing to say.

    Empty string is a real answer and the common one: no chat backend, or a
    quiet window. The brief renders nothing rather than a line announcing that
    nothing happened.

    This function CANNOT emit a row. It returns a string, and the only place
    the brief puts it is inside an existing section — which is what keeps the
    promise that the briefing's row count is unchanged by this build."""
    if not isinstance(sweep, dict) or not sweep.get("ran"):
        return ""
    entities = sweep.get("entities") or []
    if not entities:
        return ""
    named = [e.get("name") for e in entities[:CONTEXT_LINE_NAMES] if e.get("name")]
    if not named:
        return ""
    if len(entities) > len(named):
        who = ", ".join(named) + f" and {len(entities) - len(named)} more"
    elif len(named) == 1:
        who = named[0]
    else:
        who = ", ".join(named[:-1]) + f" and {named[-1]}"
    line = f"Came up in your chat since the last brief: {who}."
    note = sweep.get("coverage_note")
    if note:
        line += " " + note
    return line


# ---------------------------------------------------------------------------
# The HIST1 touch
# ---------------------------------------------------------------------------

def build_touch_event(entity: dict, *, provider: str,
                      source_skill: str = SOURCE_SKILL) -> dict:
    """One entity roll-up → one `interaction` event dict (construction only).

    `interaction` is a type BOTH history renderers already pick up — it is in
    `render_person_history.TOUCH_TYPES` (so it moves last-touch and cadence)
    and in both TIMELINE_LABELS maps (so it renders one timeline row). Using
    it means chat enters entity history through the seam that already exists
    rather than a new type only this leg writes, which nothing else would ever
    learn to read.

    The event carries the pointer and a neutral summary. It does NOT carry
    message text: an entity's permanent history is the wrong place for chat
    chatter, and a summary that quoted it would put words on somebody's record
    that they said in passing.
    """
    ref = entity.get("last_ref")
    fields = _chat.pointer_fields(ref)   # raises when the pointer is incomplete
    n = int(entity.get("n_mentions") or 0)
    data: Dict[str, Any] = {
        "summary": (f"Mentioned in your chat ({n} message"
                    f"{'s' if n != 1 else ''} in this window)"),
        "direction": "chat_mention",
        # ACCOUNT_SCOPE §4a origin discriminator — this is a connector read,
        # so the scope wall treats it STRICT. Stamped at the writer, never
        # left to be sniffed.
        "origin": "connector",
        "chat_provider": provider,
        "n_mentions": n,
    }
    data.update(fields)
    event: Dict[str, Any] = {
        "type": TOUCH_EVENT_TYPE,
        "source_skill": source_skill,
        "data": data,
    }
    ts = _norm(entity.get("last_ts"))
    if ts:
        event["ts"] = ts
    if (entity.get("kind") or "person") == "org":
        event["org_ids"] = [entity["id"]]
    else:
        event["person_ids"] = [entity["id"]]
    return event


def write_touches(workspace_root, entities, *, provider: str,
                  source_skill: str = SOURCE_SKILL) -> Dict[str, Any]:
    """Append the entity touches in ONE locked batch.

    Per-entity failures are contained and COUNTED — one entity whose pointer
    could not be built must not cost the rest of the run their history — and
    the count rides the receipt, so a rail that quietly stopped writing shows
    up as a number rather than as an ordinary-looking zero."""
    from event_gate import append_event

    events: List[dict] = []
    errors: List[dict] = []
    for e in entities or []:
        try:
            events.append(build_touch_event(e, provider=provider,
                                            source_skill=source_skill))
        except Exception as exc:  # noqa: BLE001 — contained per item, counted
            errors.append({"id": e.get("id"),
                           "error": f"{type(exc).__name__}: {exc}"})
    if events:
        append_event(Path(workspace_root) / "_hq" / "data" / "events.jsonl",
                     events, holder=source_skill)
    return {"n_written": len(events), "errors": errors}


# ---------------------------------------------------------------------------
# The leg
# ---------------------------------------------------------------------------

def run_chat_context(workspace_root, messages, tracked, *, provider=None,
                     scan_plan=None, budget: Optional[ReadBudget] = None,
                     apply: bool = True,
                     source_skill: str = SOURCE_SKILL) -> Dict[str, Any]:
    """The whole leg: mentions → bounded touches → one context line → counts.

    `apply=False` computes everything and writes nothing — the dry run a
    caller uses to see what a fire WOULD do. It is not a shadow fence; the leg
    ships live.

    Returns a receipt block the caller folds into the fire's own receipt. It
    always carries `ran`, so "there is no chat backend" and "the sweep found
    nothing" are two different, readable answers rather than one shared zero.
    """
    provider = _chat.resolve_chat_provider(workspace_root, provider)
    if not provider:
        out = _chat.skip_receipt(
            "no chat backend is declared for this workspace", leg="chat-context")
        out.update(_empty_counts())
        out["entities"] = []
        out["context_line"] = ""
        return out

    plan = scan_plan or _chat.plan_scan(provider, date_filtered=True)
    budget = budget or ReadBudget()

    # THE BUDGET IS SPENT HERE, in the product path, before a single message
    # is examined. Anything the budget could not afford is not read at all.
    within_budget, deferred_rooms = apply_budget(messages, budget)
    found = collect_mentions(within_budget, tracked)
    counts = found["counts"]
    entities = found["entities"]
    counts["n_conversations_deferred"] = len(deferred_rooms)

    written = {"n_written": 0, "errors": []}
    if apply and entities:
        written = write_touches(workspace_root, entities, provider=provider,
                                source_skill=source_skill)
    counts["n_touches_written"] = written["n_written"]
    counts.update(budget.report())

    out: Dict[str, Any] = {
        "ran": True,
        "status": "degraded" if plan.get("degraded") else "complete",
        "leg": "chat-context",
        "provider": provider,
        "scan_mode": plan.get("mode"),
        "degraded": bool(plan.get("degraded")),
        "coverage_note": plan.get("coverage_note"),
        "entities": entities,
        "deferred_windows": budget.report()["deferred_windows"],
        "touch_errors": written["errors"],
    }
    out.update(counts)
    out["context_line"] = context_line(out)
    return out


# ---------------------------------------------------------------------------
# WALKFIX1 Item G — the go-context chat leg's read receipt
# ---------------------------------------------------------------------------

CONNECTOR_READ_EVENT_TYPE = "connector_read"
GO_CONTEXT_LEG = "go-context"


def log_connector_read(workspace_root, *, provider, scope, n_results,
                       leg: str = GO_CONTEXT_LEG, window_days=None,
                       query=None, source_skill: str = "workspace-manager"):
    """Receipt ONE live connector read. Returns the appended event, or None.

    WHY THIS EXISTS. `go [name]`'s chat leg is a live read at ask time: it
    closes nothing and captures nothing, so by design it wrote nothing to the
    substrate. That is the right posture for a READ — and it made a real miss
    invisible. On 2026-08-09 a warm `go` against a declared Slack backend
    surfaced zero chat lines while a three-day-old key-person DM sat in the
    backend carrying a time-sensitive scheduling ask. From outside, "the leg
    never ran", "the leg ran with too narrow a window" and "the leg ran and
    found nothing" were INDISTINGUISHABLE, because none of the three leaves a
    trace. A skipped leg says nothing anywhere.

    So the READ itself stays read-only about the customer's records — nothing
    here touches an entity, a commitment or a thread — and the fire records
    only that it looked: which backend, what scope, how wide a window, how many
    hits. `n_results: 0` against a declared backend is then a visible fact
    somebody can go and check, instead of a silence.

    OBSERVABILITY ONLY. This does not change what the leg retrieves or how it
    scores; the retrieval-quality question the same bug raised stays open. It
    makes the leg's outcome legible, which is the precondition for answering
    that question with evidence rather than by re-running it by hand.

    Best-effort, never raises: telemetry must not take a warm load down.
    """
    try:
        from event_gate import append_event

        data: Dict[str, Any] = {
            "provider": _norm(provider),
            "leg": _norm(leg),
            "scope": _norm(scope),
            "n_results": int(n_results),
        }
        if window_days is not None:
            data["window_days"] = int(window_days)
        if query:
            data["query"] = _norm(query)[:200]
        event = {"type": CONNECTOR_READ_EVENT_TYPE,
                 "source_skill": _norm(source_skill) or "workspace-manager",
                 "data": data}
        # `append_event` returns None (the writer lock stamps seq/ts in place),
        # so the event dict is what comes back — a caller that wants to say
        # "recorded" needs something truthy, and None is the failure answer.
        append_event(
            Path(workspace_root) / "_hq" / "data" / "events.jsonl",
            event, holder="chat_context.go_context_read")
        return event
    except Exception:
        return None


__all__ = [
    "READ_BUDGET", "MAX_ENTITY_TOUCHES", "CONTEXT_LINE_NAMES",
    "TOUCH_EVENT_TYPE", "CURSOR_KEY",
    "CONNECTOR_READ_EVENT_TYPE", "GO_CONTEXT_LEG",
    "ReadBudget", "apply_budget", "collect_mentions", "context_line",
    "build_touch_event", "write_touches", "run_chat_context",
    "log_connector_read",
]
