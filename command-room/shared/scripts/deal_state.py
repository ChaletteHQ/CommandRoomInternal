#!/usr/bin/env python3
"""
deal_state.py — THE deal writer / single closure path (SPEC PIPE1, 2026-07).

WHY THIS EXISTS
===============
Before PIPE1, deal status was smuggled into engagement `label` free-text by
workspace-manager ("Active sales conversation — …") — the exact
logic-in-prose bug class (Bug #99) that turned engagement labels into an
unqueryable status store. And board-pack-assembler §4 read "lost-deal events"
that nothing ever wrote. This module is the one writer of every `deal.*`
field and every `deal_*` event, mirroring commitment_state's doctrine:

  - ONE closure path: `close_deal()` is the only way a deal reaches a
    terminal outcome. Idempotent (`already_closed` is a NO-OP, never a
    duplicate event), loud on bad input (`lost` without a valid loss_reason
    is rejected), and it flips the thread status in the same call
    (won → resolved, lost → archived).
  - NO hand-rolled writes: entity mutations route through
    thread_writer.create_thread / update_thread (schema validation, atomic
    locked write) and events route through event_gate.append_event (the
    Phase 1 gate) — there is no direct open(...) write anywhere here.
  - The D6 one-utterance contract: a user-EXPLICIT win declaration on a
    prospect-org deal ("Acme signed") closes the deal AND runs the SAME
    prospect→client conversion path workspace-manager uses
    (org_writer.update_org flip + engagement edge), atomically — pass
    convert_prospect=True. The plain `mark [deal] won` verb does NOT
    convert; it returns a conversion suggestion for the skill to render
    (acceptance §7 items 5 + 9). Detector-observed signals (Part 2) never
    reach this module directly — they propose, the user confirms.

Stages are the fixed v1 set (thread_writer.DEAL_STAGES); won/lost are the
terminal `outcome`, never stages. Money is user-stated only — nothing in
this module estimates a value (quantify.py discipline).

stdlib only.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import thread_archive  # noqa: E402
import thread_writer  # noqa: E402
from thread_writer import (  # noqa: E402
    ALLOWED_DEAL_FIELDS,
    DEAL_FORECAST_CATEGORIES,
    DEAL_LOSS_REASONS,
    DEAL_OUTCOMES,
    DEAL_STAGES,
)
from entities_io import entities_collection  # noqa: E402

# UXC1 (2026-07-21 ruling) — plain-English display labels for the wire enums.
# Pickers, acks, and any rendered stage/reason text use THESE; the snake_case
# ids stay wire-only (they are banned vocabulary in anything the CEO reads).
# Pinned by test to cover the enum sets exactly — a new stage/reason without
# a display label goes red at the pin, never renders raw.
STAGE_DISPLAY = {
    "lead": "Lead",
    "qualified": "Qualified",
    "proposal_sent": "Proposal sent",
    "negotiating": "Negotiating",
}
LOSS_REASON_DISPLAY = {
    "no_decision": "No decision",
    "price": "Price",
    "competitor": "Competitor",
    "diy": "Doing it themselves",
    "timing": "Timing",
    "bad_fit": "Bad fit",
    "other": "Other",
}


class DealStateError(ValueError):
    """A deal write was refused. Fail loud — silent fallthrough is how
    engagement labels became a free-text status store."""


# Fields update_deal may touch. Stage moves go through set_stage;
# outcome/loss_reason/closed_at go through close_deal — no other path.
UPDATABLE_DEAL_FIELDS = {
    "value", "currency", "expected_close", "forecast_category", "source",
}


def _today() -> str:
    return datetime.date.today().isoformat()


def _events_path(ws: Path) -> Path:
    return Path(ws) / "_hq" / "data" / "events.jsonl"


def _load_entities(ws: Path) -> dict:
    p = Path(ws) / "_hq" / "data" / "entities.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _threads(data: dict) -> list:
    """SPEC DUALKEY1: `entities_collection(data, "projects")` is now an
    alias for the canonical `threads` list — a single call is safe by
    construction, no more dual-fetch-and-prefer."""
    return entities_collection(data, "projects")


def _find_thread(data: dict, thread_id: str) -> Optional[dict]:
    return next((t for t in _threads(data) if t.get("id") == thread_id), None)


def _find_org(data: dict, org_id: str) -> Optional[dict]:
    return next((o for o in entities_collection(data, "orgs")
                 if o.get("id") == org_id), None)


def _append(ws: Path, event, source_skill: str) -> list:
    """Gated append. Takes one event or a list — a list lands in ONE append, so
    two events written for the same transition cannot half-land. Returns the
    written rows (seq-stamped) — CUTB item 4 reads the `deal_won` seq off it
    for the promotion receipt."""
    from event_gate import append_event
    events = [event] if isinstance(event, dict) else list(event)
    return append_event(_events_path(ws), events, holder=source_skill)


def _require_deal_thread(thread: Optional[dict], thread_id: str) -> dict:
    if thread is None:
        raise DealStateError(f"thread not found: {thread_id!r}")
    if thread.get("kind") != "deal":
        raise DealStateError(
            f"thread {thread_id!r} is kind={thread.get('kind')!r}, not a deal "
            "thread — deal_state only writes kind='deal' threads")
    return thread


def _require_open(deal: dict, thread_id: str) -> None:
    if deal.get("outcome") in DEAL_OUTCOMES:
        raise DealStateError(
            f"deal {thread_id!r} is already closed ({deal['outcome']}) — "
            "terminal deals are not editable; open a new deal thread for a "
            "new opportunity with this org")


def _validate_stage(stage: str) -> None:
    if stage not in DEAL_STAGES:
        raise DealStateError(
            f"invalid deal stage {stage!r} — must be one of {list(DEAL_STAGES)} "
            "(won/lost are outcomes, closed via close_deal)")


def _thread_org_id(t: dict) -> str:
    """A thread's org across the shapes in the wild (org / org_id /
    affiliation_id / affiliation_ids[0])."""
    return (t.get("org") or t.get("org_id") or t.get("affiliation_id")
            or (t.get("affiliation_ids") or [None])[0] or "")


def org_deal_coverage(threads: list, org_id: str) -> Optional[dict]:
    """FS-18b — THE shared existence predicate for "is this org's deal
    activity already tracked?". Returns the covering thread, or None.

    Covered when the org carries EITHER:
      (a) an OPEN kind='deal' thread (status not resolved/archived), or
      (b) an ACTIVE ENGAGEMENT THREAD — any non-deal thread affiliated with
          the org whose status isn't terminal. An active engagement IS
          tracked coverage (RV-5 M ruling): proposing deal CREATION for such
          an org produces a proposal whose confirm the create path refuses
          (the Summit zombie — confirm errored forever, proposal stayed
          open).

    ONE helper, two consumers, never forked: `deal_signal_detector` consults
    it before emitting a deal_creation proposal, and the apply-choices
    `deal_creation` confirm handler consults it BEFORE `create_deal` (a
    covered org resolves the proposal declined with an honest ack instead of
    erroring — which also self-heals any zombie already in a live queue).
    """
    fallback = None
    for t in threads or []:
        if not isinstance(t, dict):
            continue
        if _thread_org_id(t) != org_id:
            continue
        if t.get("status") in ("resolved", "archived"):
            continue
        if t.get("kind") == "deal":
            deal = t.get("deal")
            if isinstance(deal, dict) and deal.get("outcome") in DEAL_OUTCOMES:
                continue  # terminal deal — not coverage
            return t  # open deal thread: the strongest coverage
        elif fallback is None:
            fallback = t  # active engagement thread
    return fallback


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def create_deal(
    workspace_root,
    *,
    name: str,
    org_id: str,
    stage: str = "lead",
    value: Optional[float] = None,
    currency: str = "USD",
    expected_close: Optional[str] = None,
    forecast_category: Optional[str] = None,
    source: Optional[str] = None,
    owner_person_id: Optional[str] = None,
    source_skill: str = "pipeline-tracker",
    skip_dedup: bool = False,
) -> dict:
    """Open a new deal: a kind='deal' thread carrying the nested deal object,
    plus a `deal_created` event. The org must already exist (create it via
    workspace-manager's `new prospect` / org_writer first) — a deal always
    hangs off a real org edge.

    `value` is the user's stated number or None — NEVER an estimate; ranges
    are out of v1 scope (store None + a note in `source`).

    Raises `thread_writer.DuplicateDealError` when the org already carries an
    open deal (ENTITY1 §4c) — the exception's `.existing` is that thread, so
    the caller can offer a merge. `skip_dedup=True` is the deliberate
    two-engagements override and is passed through to the writer; it belongs
    to the user's explicit confirmation, never to a retry loop.
    """
    ws = Path(workspace_root)
    _validate_stage(stage)

    data = _load_entities(ws)
    if _find_org(data, org_id) is None:
        raise DealStateError(
            f"org_id={org_id!r} does not reference an existing org. Create "
            "the org first (org_writer.create_org / 'new prospect [Name]'), "
            "then open the deal.")

    deal: dict[str, Any] = {
        "stage": stage,
        "stage_entered": _today(),
        "opened_at": _today(),
    }
    if value is not None:
        deal["value"] = value
        deal["currency"] = currency
    if expected_close is not None:
        deal["expected_close"] = expected_close
    if forecast_category is not None:
        deal["forecast_category"] = forecast_category
    if source is not None:
        deal["source"] = source

    thread = thread_writer.create_thread(
        ws,
        canonical_name=name,
        kind="deal",
        org_id=org_id,
        owner_person_id=owner_person_id,
        deal=deal,
        source_skill=source_skill,
        skip_dedup=skip_dedup,
    )

    ev_data: dict[str, Any] = {
        "thread_id": thread["id"],
        "org_id": org_id,
        "stage": stage,
        "name": thread.get("canonical_name"),
    }
    if value is not None:
        ev_data["value"] = value
    _append(ws, {
        "type": "deal_created",
        "source_skill": source_skill,
        "primary_thread_id": thread["id"],
        "org_ids": [org_id],
        "data": ev_data,
    }, source_skill)
    return thread


def adopt_deal(
    workspace_root,
    thread_id: str,
    *,
    stage: str = "lead",
    value: Optional[float] = None,
    currency: str = "USD",
    expected_close: Optional[str] = None,
    source: Optional[str] = None,
    source_skill: str = "pipeline-tracker",
) -> dict:
    """Attach a deal object to a pre-PIPE1 kind='deal' thread that has none —
    the one-tap 'track this as a pipeline deal?' adoption path (real-data
    fixture gotcha: live workspaces carry deal threads that predate the deal
    object; readers must not crash on them, and this is how they graduate).
    Refuses a thread that already carries a deal object.
    """
    ws = Path(workspace_root)
    _validate_stage(stage)
    data = _load_entities(ws)
    thread = _require_deal_thread(_find_thread(data, thread_id), thread_id)
    if isinstance(thread.get("deal"), dict):
        raise DealStateError(
            f"thread {thread_id!r} already carries a deal object — use "
            "update_deal / set_stage / close_deal")

    deal: dict[str, Any] = {
        "stage": stage,
        "stage_entered": _today(),
        "opened_at": thread.get("first_seen") or _today(),
    }
    if value is not None:
        deal["value"] = value
        deal["currency"] = currency
    if expected_close is not None:
        deal["expected_close"] = expected_close
    if source is not None:
        deal["source"] = source

    updated = thread_writer.update_thread(
        ws, thread_id, deal=deal, source_skill=source_skill)

    org_id = updated.get("affiliation_id") or updated.get("org_id") or ""
    ev_data: dict[str, Any] = {
        "thread_id": thread_id,
        "org_id": org_id,
        "stage": stage,
        "name": updated.get("canonical_name") or updated.get("display_name"),
        "adopted": True,
    }
    if value is not None:
        ev_data["value"] = value
    _append(ws, {
        "type": "deal_created",
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "org_ids": [org_id] if org_id and org_id != "personal" else [],
        "data": ev_data,
    }, source_skill)
    return updated


def update_deal(
    workspace_root,
    thread_id: str,
    *,
    source_skill: str = "pipeline-tracker",
    **fields,
) -> dict:
    """Update non-stage deal fields (value / currency / expected_close /
    forecast_category / source) on an OPEN deal. Stage moves are set_stage;
    terminal outcomes are close_deal — this function refuses both."""
    ws = Path(workspace_root)
    bad = set(fields) - UPDATABLE_DEAL_FIELDS
    if bad:
        raise DealStateError(
            f"update_deal cannot touch {sorted(bad)} — stage moves go through "
            "set_stage, outcomes through close_deal, and unknown fields need "
            "a schema change first. Updatable: "
            f"{sorted(UPDATABLE_DEAL_FIELDS)}")
    if not fields:
        raise DealStateError("update_deal needs at least one field to change")

    data = _load_entities(ws)
    thread = _require_deal_thread(_find_thread(data, thread_id), thread_id)
    deal = thread.get("deal")
    if not isinstance(deal, dict):
        raise DealStateError(
            f"thread {thread_id!r} has no deal object — an untracked deal "
            "thread; adopt it first via adopt_deal")
    _require_open(deal, thread_id)

    new_deal = dict(deal)
    changed: dict[str, Any] = {}
    for k, v in fields.items():
        if new_deal.get(k) != v:
            new_deal[k] = v
            changed[k] = v
    if not changed:
        return {"status": "unchanged", "thread_id": thread_id}

    thread_writer.update_thread(ws, thread_id, deal=new_deal,
                                source_skill=source_skill)
    ev_data = {"thread_id": thread_id, **changed}
    _append(ws, {
        "type": "deal_updated",
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "data": ev_data,
    }, source_skill)
    return {"status": "updated", "thread_id": thread_id, "changed": changed}


def set_stage(
    workspace_root,
    thread_id: str,
    to_stage: str,
    *,
    note: Optional[str] = None,
    source_skill: str = "pipeline-tracker",
) -> dict:
    """Move an open deal between active stages. Backward moves are allowed
    (real deals regress) — `deal_stage_changed` records direction and
    days-in-stage resets either way. Stamps `stage_entered`."""
    ws = Path(workspace_root)
    _validate_stage(to_stage)
    data = _load_entities(ws)
    thread = _require_deal_thread(_find_thread(data, thread_id), thread_id)
    deal = thread.get("deal")
    if not isinstance(deal, dict):
        raise DealStateError(
            f"thread {thread_id!r} has no deal object — adopt it first via "
            "adopt_deal")
    _require_open(deal, thread_id)

    from_stage = deal.get("stage")
    if from_stage == to_stage:
        return {"status": "unchanged", "thread_id": thread_id,
                "stage": to_stage}

    new_deal = dict(deal)
    new_deal["stage"] = to_stage
    new_deal["stage_entered"] = _today()
    thread_writer.update_thread(ws, thread_id, deal=new_deal,
                                source_skill=source_skill)
    ev_data: dict[str, Any] = {
        "thread_id": thread_id,
        "from_stage": from_stage,
        "to_stage": to_stage,
    }
    if note:
        ev_data["note"] = str(note)[:200]
    _append(ws, {
        "type": "deal_stage_changed",
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "data": ev_data,
    }, source_skill)
    return {"status": "moved", "thread_id": thread_id,
            "from_stage": from_stage, "to_stage": to_stage}


def close_deal(
    workspace_root,
    thread_id: str,
    outcome: str,
    *,
    loss_reason: Optional[str] = None,
    loss_note: Optional[str] = None,
    value: Optional[float] = None,
    convert_prospect: bool = False,
    source_skill: str = "pipeline-tracker",
    deal_manufactured: bool = False,
) -> dict:
    """THE closure path. Every deal terminal — 'mark [deal] won/lost',
    '[Name] signed', 'we lost the [deal]', a confirmed detector proposal —
    lands here.

      - `outcome` ∈ won | lost. `lost` REQUIRES a valid loss_reason
        (rejected loudly without one — no reason-less tombstones).
      - Idempotent: an already-closed deal returns
        {"status": "already_closed"} and writes NOTHING (no duplicate
        terminal event). Callers honor it as a NO-OP.
      - Flips the thread status in the same write: won → 'resolved',
        lost → 'archived'.
      - `value`: a stated-at-close figure ("closed at $40k") updates the
        deal value; None leaves it untouched. Never estimated.
      - `convert_prospect=True` (D6 — ONLY for a user-EXPLICIT win
        declaration like "[Name] signed" / "closed the deal with [Name]"):
        when the deal's org is a prospect, the win converts it — and since
        CUTB item 3 (2026-09-06) that conversion runs through the SAME
        receipted, undoable path an automatic promotion uses
        (`org_promotion.promote_org`: `org_promoted` receipt, batch id, the
        CHANGED line, `undo`). The old inline conversion wrote NO receipt
        and NO batch — a person who said "[Name] signed" got a client with
        no CHANGED line and nothing to undo (v5.28.0 attended test B4.2).
        What the flag still means: the person's explicit word overrides a
        standing undo (`explicit=True` — a prior `undo` never blocks a
        conversion the person just asked for by name). Preconditions (a
        resolvable primary-focus org) are still checked BEFORE any write
        so a refused conversion leaves nothing half-done. With
        convert_prospect=False on a prospect-org win, DEALNAG1's automatic
        promotion runs instead (M ruling 4) — same path, same receipt.
        Detector-observed signals never set this flag.
      - `deal_manufactured=True` is set ONLY by `win_org_without_deal` (the
        `mark [org] won` path when no deal was on file): it is stamped on
        the promotion receipt so `undo` knows to put the deal back too
        (thread archived, the won event reversed — CUTB item 4).

    Returns {"status": "closed", "outcome", "thread_id", "org_id",
    "converted": bool, "promoted": bool, "promotion_batch_id": str|None,
    "conversion_suggestion": str|None, "won_seq": int|None,
    "event": {...}} or {"status": "already_closed", ...}.
    """
    ws = Path(workspace_root)
    if outcome not in DEAL_OUTCOMES:
        raise DealStateError(
            f"outcome must be one of {list(DEAL_OUTCOMES)}, got: {outcome!r}")
    if outcome == "lost":
        if loss_reason not in DEAL_LOSS_REASONS:
            raise DealStateError(
                "closing a deal as lost REQUIRES a loss_reason — one of "
                f"{list(DEAL_LOSS_REASONS)} (got: {loss_reason!r}). Ask the "
                "user; never guess or omit.")
    elif loss_reason is not None or loss_note is not None:
        raise DealStateError("loss_reason/loss_note only apply to outcome='lost'")

    data = _load_entities(ws)
    thread = _require_deal_thread(_find_thread(data, thread_id), thread_id)
    deal = thread.get("deal")
    if not isinstance(deal, dict):
        raise DealStateError(
            f"thread {thread_id!r} has no deal object — adopt it first via "
            "adopt_deal, then close")

    org_id = thread.get("affiliation_id") or thread.get("org_id") or ""

    # Idempotency: terminal is terminal. No second event, no status churn.
    if deal.get("outcome") in DEAL_OUTCOMES:
        return {
            "status": "already_closed",
            "thread_id": thread_id,
            "outcome": deal["outcome"],
            "org_id": org_id,
        }

    # D6 conversion pre-checks BEFORE any write (atomic-or-nothing).
    org = _find_org(data, org_id) if org_id and org_id != "personal" else None
    org_is_prospect = bool(org and org.get("relationship_type") == "prospect")
    focus_org = None
    if convert_prospect and outcome == "won" and org_is_prospect:
        focus_org = next(
            (o for o in entities_collection(data, "orgs")
             if o.get("is_primary_focus")), None)
        if focus_org is None:
            raise DealStateError(
                "cannot run the prospect->client conversion: no "
                "is_primary_focus org is set — ASK the user which of their "
                "orgs this client is for, then re-run; do NOT guess the "
                "engagement source. (Nothing was written.)")

    new_deal = dict(deal)
    new_deal["outcome"] = outcome
    new_deal["closed_at"] = _today()
    if outcome == "lost":
        new_deal["loss_reason"] = loss_reason
        if loss_note:
            new_deal["loss_note"] = str(loss_note)[:300]
    if value is not None:
        new_deal["value"] = value
        new_deal.setdefault("currency", "USD")

    thread_status = "resolved" if outcome == "won" else "archived"
    # SPEC RIDERS1 item 2 — the ARCHFIX gap, one object over. This leg has
    # always landed `status: "archived"` on a lost deal's thread while stamping
    # neither `archived_at` (MASTER_TRACKER's sort key for Recently Archived,
    # so the row sorted under "" and fell off the list) nor any timeline event
    # (measured live: a deal-leg archive wrote the record and nothing else).
    #
    # It does NOT route through `thread_archive.archive_thread`: the closed deal
    # object and the status are ONE atomic record write here, and archiving
    # first would open a window where the thread is archived with an open deal
    # still on it. So the stamps are made in the SAME update_thread call, and
    # the event is built by the shared builder rather than hand-copied.
    from_status = thread.get("status")
    fields: dict[str, Any] = {"deal": new_deal, "status": thread_status}
    archive_reason = None
    if thread_status == thread_archive.ARCHIVED_STATUS:
        archive_reason = thread_archive.normalize_reason(
            f"deal closed lost ({loss_reason})" if loss_reason
            else "deal closed lost")
        fields["archived_at"] = thread_archive.archive_stamp()
        if archive_reason is not None:
            fields["archive_reason"] = archive_reason
    thread_writer.update_thread(
        ws, thread_id, source_skill=source_skill, **fields)

    ev_data: dict[str, Any] = {"thread_id": thread_id, "org_id": org_id}
    final_value = new_deal.get("value")
    if final_value is not None:
        ev_data["value"] = final_value
    if outcome == "lost":
        ev_data["loss_reason"] = loss_reason
        if loss_note:
            ev_data["loss_note"] = str(loss_note)[:300]

    # CUTB item 3 — the explicit conversion is NOT run inline any more. It
    # goes through `org_promotion.promote_org` below (receipt, batch id,
    # CHANGED line, undo) exactly like the automatic one; `explicit_convert`
    # only records that the person asked for it by name. The `deal_won`
    # event keeps its `converted_prospect` stamp for that utterance (the
    # payload contract), written before the promotion lands because the
    # receipt must FOLLOW the fact it names (PID1) and needs this event's
    # seq.
    explicit_convert = focus_org is not None
    if explicit_convert:
        ev_data["converted_prospect"] = True

    event = {
        "type": "deal_won" if outcome == "won" else "deal_lost",
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "org_ids": [org_id] if org_id and org_id != "personal" else [],
        "data": ev_data,
    }
    # Record first, event second (the ARCHFIX order) — and both events in ONE
    # gated append, so a lost deal cannot end up with its outcome on the
    # timeline and its archive missing from it.
    to_append = [event]
    if thread_status == thread_archive.ARCHIVED_STATUS:
        to_append.append(thread_archive.build_status_change_event(
            thread_id, from_status=from_status, reason=archive_reason,
            source_skill=source_skill))
    written = _append(ws, to_append, source_skill)
    won_seq = None
    if outcome == "won":
        from event_seq import event_seq as _event_seq
        try:
            won_seq = _event_seq((written or [None])[0])
        except Exception:  # noqa: BLE001 — the seq is an anchor, not a gate
            won_seq = None

    # DEALNAG1 — the outcome retires every open deal-signal proposal bound
    # to this thread or org in the SAME turn (superseded, note=deal_won /
    # deal_lost). A won deal's "is a live deal" nag was surviving the win by
    # weeks because nothing here told the proposal its question was
    # answered. Best-effort: a retirement failure must never unwind a close
    # that already landed on the record.
    n_retired = 0
    retire_error = None
    try:
        from deal_signal_retire import retire_deal_proposals

        r1 = retire_deal_proposals(
            ws, reason=f"deal_{outcome}", source_skill=source_skill,
            thread_id=thread_id,
            org_id=org_id if org_id and org_id != "personal" else None)
        n_retired = r1["n_retired"]
    except Exception as exc:  # noqa: BLE001
        # REVIEW DEALNAG1 F4 — best-effort, but never SILENT: a broken
        # import or a gate refusal here would regress the whole fix with no
        # trace, and "0 retired" would read as "nothing to retire".
        n_retired = 0
        retire_error = f"{type(exc).__name__}: {exc}"
        print(f"close_deal: deal-signal retirement failed for {thread_id}: "
              f"{retire_error}", file=sys.stderr)

    # DEALNAG1 (M ruling 4, 2026-09-03) — a WON deal on a prospect org
    # PROMOTES it, automatically, with a receipt and an undo. The old
    # behavior returned `conversion_suggestion` for the skill to render as
    # "say `[Name] is now a client`" — a question about a fact the
    # workspace had just written down, which is the defect the ruling
    # names. CUTB item 3 (2026-09-06): the explicit `convert_prospect=True`
    # family ("[Name] signed") comes through HERE too now — `not converted`
    # used to be the switch that sent every explicit conversion round the
    # silent inline path with no receipt and no undo. Preconditions live in
    # org_promotion (a primary-focus org, no prior undo unless explicit); a
    # skip returns the suggestion so the person still learns the manual
    # path. The receipt carries the deal that drove it (`deal_thread_id`,
    # `won_seq`) and whether this call manufactured that deal
    # (`deal_manufactured`) — the anchors `undo` puts back (item 4).
    promoted = False
    promotion_skipped = None
    promotion_batch_id = None
    if outcome == "won" and org_is_prospect and org_id:
        try:
            from org_promotion import promote_org

            pres = promote_org(ws, org_id, reason="deal_won",
                               since=new_deal.get("closed_at") or "",
                               source_skill=source_skill,
                               deal_thread_id=thread_id, won_seq=won_seq,
                               deal_manufactured=bool(deal_manufactured),
                               explicit=explicit_convert)
            promoted = pres.get("status") == "promoted"
            if promoted:
                promotion_batch_id = pres.get("batch_id")
            else:
                promotion_skipped = pres.get("reason")
        except Exception as exc:  # noqa: BLE001
            promotion_skipped = f"{type(exc).__name__}: {exc}"
            print(f"close_deal: automatic promotion failed for {org_id}: "
                  f"{promotion_skipped}", file=sys.stderr)

    suggestion = None
    if outcome == "won" and org_is_prospect and not promoted:
        org_name = (org or {}).get("canonical_name") or org_id
        suggestion = f"{org_name} is now a client"

    return {
        "status": "closed",
        "thread_id": thread_id,
        "outcome": outcome,
        "org_id": org_id,
        "converted": promoted,
        "promoted": promoted,
        "promotion_batch_id": promotion_batch_id,
        "promotion_skipped": promotion_skipped,
        "conversion_suggestion": suggestion,
        "deal_manufactured": bool(deal_manufactured),
        "won_seq": won_seq,
        "n_proposals_retired": n_retired,
        "retire_error": retire_error,
        "event": event,
    }


def win_org_without_deal(
    workspace_root,
    org_id: str,
    *,
    name: Optional[str] = None,
    value: Optional[float] = None,
    source_skill: str = "pipeline-tracker",
) -> dict:
    """CUTB item 3 (2026-09-06) — `mark [org] won` when the org has NO deal
    record. The v5.28.0 attended test (B4.2) saw the chat manufacture a deal
    from a detector proposal and close it in the same breath, with no
    receipt naming the invention, no CHANGED line and no undo. The
    coordinator's ruling (M's act-don't-ask preference): CREATE THE DEAL
    OPENLY — one receipt that says so, the promotion through the receipted
    path, and an `undo` that puts everything back (thread archived, won
    event reversed, org a prospect again).

    Honest no-ops, nothing written: an OPEN deal on file (`has_open_deal` —
    the ordinary `mark [deal] won` owns it; the return names the thread(s)),
    a deal already closed won (`already_closed`), an org already a client
    with no deal at all (`already_client`), and — REVIEW CUTB F-1 — no
    primary-focus org set (`no_primary_focus`: the D6 posture, checked
    BEFORE `create_deal`; the return carries the one-line `ack` that says
    why). Otherwise: `create_deal` (stage lead, `deal.source` names this
    path) -> `close_deal(..., won, convert_prospect=True,
    deal_manufactured=True)`. `convert_prospect=True` because `mark [org]
    won` IS the person's explicit word, exactly like `[Name] signed`: the
    promotion runs `explicit=True`, so a standing undo of an EARLIER
    automatic promotion never orphans the deal this act just invented
    (F-1's shape: a won deal on the books with no batch and no undo). The
    return is `close_deal`'s plus `manufactured_deal: True`, `deal_name`,
    `receipt_line` (the sentence the ack MUST carry, verbatim) and `ack`
    (receipt_line + the promotion's own sentence with the standing
    `undo`)."""
    ws = Path(workspace_root)
    data = _load_entities(ws)
    org = _find_org(data, org_id)
    if org is None:
        raise DealStateError(
            f"org_id={org_id!r} does not reference an existing org — resolve "
            "the name first; never invent an org to hang a win on.")
    org_name = org.get("canonical_name") or org_id
    opens = [r for r in list_open_deals(ws) if r.get("org_id") == org_id]
    if opens:
        return {"status": "has_open_deal", "org_id": org_id,
                "org_name": org_name,
                "thread_ids": [r["thread_id"] for r in opens],
                "deal_names": [r["name"] for r in opens]}
    won = [r for r in list_closed_deals(ws)
           if r.get("org_id") == org_id
           and (r.get("deal") or {}).get("outcome") == "won"]
    if won:
        last = won[-1]
        return {"status": "already_closed", "org_id": org_id,
                "org_name": org_name, "thread_id": last["thread_id"],
                "outcome": "won",
                "closed_at": (last.get("deal") or {}).get("closed_at")}
    if org.get("relationship_type") == "client":
        return {"status": "already_client", "org_id": org_id,
                "org_name": org_name}
    # REVIEW CUTB F-1 — the promotion's one blocking precondition is checked
    # BEFORE the deal is written. Without this, `create_deal` + `close_deal`
    # landed a closed-won deal on the books (closed-deals list, won-rate
    # tile) and THEN the promotion skipped `no_primary_focus`: an automatic
    # write with no batch and no `undo`. Same honest-no-op family as above.
    from org_promotion import primary_focus_org
    if primary_focus_org(data) is None:
        return {"status": "no_primary_focus", "org_id": org_id,
                "org_name": org_name,
                "ack": (f"Nothing on file for {org_name} and no primary-focus "
                        f"org is set, so I did not open a deal — tell me which "
                        f"of your orgs this client is for first.")}

    deal_name = (name or "").strip() or f"{org_name} deal"
    thread = create_deal(
        ws, name=deal_name, org_id=org_id, stage="lead", value=value,
        source="opened by `mark [org] won` — no deal was on file",
        source_skill=source_skill)
    # `convert_prospect=True`: the person's own word (F-1) — the promotion
    # runs `explicit=True`, so a prior undo of an automatic promotion does
    # not leave the manufactured deal standing with nothing to reverse it.
    res = close_deal(ws, thread["id"], "won", source_skill=source_skill,
                     convert_prospect=True, deal_manufactured=True)
    receipt_line = (f"No deal was on file for {org_name}, so I opened one "
                    f"and closed it won.")
    if res.get("promoted"):
        ack = (f"{receipt_line} {org_name} is a client now — say `undo` to "
               f"put it all back.")
    else:
        ack = (f"{receipt_line} {org_name} is still marked a prospect — say "
               f"`{org_name} is now a client` and I'll convert them.")
    res.update({"manufactured_deal": True, "deal_name": deal_name,
                "org_name": org_name, "receipt_line": receipt_line,
                "ack": ack})
    return res


# ---------------------------------------------------------------------------
# Readers (defensive)
# ---------------------------------------------------------------------------

def list_open_deals(workspace_root) -> list[dict]:
    """Every open deal thread, defensively read. One row per kind='deal'
    thread that is not archived/resolved and has no terminal outcome:

      {thread_id, name, org_id, status, deal (dict|None), untracked (bool)}

    `untracked=True` = a pre-PIPE1 deal thread with NO deal object (the
    real-data shape live workspaces carry). Readers render these as
    "untracked deal thread" rows and offer one-tap adoption — never crash,
    never silently drop.
    """
    ws = Path(workspace_root)
    try:
        data = _load_entities(ws)
    except (OSError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for t in _threads(data):
        if not isinstance(t, dict) or t.get("kind") != "deal":
            continue
        if t.get("status") in ("archived", "resolved"):
            continue
        deal = t.get("deal") if isinstance(t.get("deal"), dict) else None
        if deal and deal.get("outcome") in DEAL_OUTCOMES:
            continue
        out.append({
            "thread_id": t.get("id"),
            "name": t.get("canonical_name") or t.get("display_name") or t.get("id"),
            "org_id": t.get("affiliation_id") or t.get("org_id"),
            "status": t.get("status"),
            "deal": deal,
            "untracked": deal is None,
        })
    return out


def won_reversals(workspace_root) -> dict[str, dict]:
    """CUTB item 4 — thread_id -> the `deal_won_reversed` marker `brain_undo`
    wrote when a promotion whose deal was manufactured by `mark [org] won`
    was undone. Add-beside vocabulary (the `promotion_reversed` precedent):
    the `deal_won` event and the deal object are never rewritten; every won
    reader folds THIS. Org-scoped full-history read through events_io."""
    from events_io import load_events_org_scoped

    ws = Path(workspace_root)
    if not _events_path(ws).exists():
        return {}
    try:
        events, _skipped = load_events_org_scoped(ws)
    except Exception:  # noqa: BLE001 — a reader never raises
        return {}
    out: dict[str, dict] = {}
    for ev in events:
        if ev.get("type") != "deal_won_reversed":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        tid = d.get("thread_id")
        if tid:
            out[str(tid)] = {"won_seq": d.get("won_seq"),
                             "reversed_by": d.get("reversed_by"),
                             "ts": ev.get("ts"), "seq": ev.get("seq")}
    return out


def list_closed_deals(workspace_root) -> list[dict]:
    """Closed deals (terminal outcome on the deal object). Same row shape as
    list_open_deals plus the deal carries outcome/closed_at — the won-cycle
    and won-rate inputs pipeline_math reads.

    CUTB item 4: a won deal whose win was REVERSED by an undo
    (`won_reversals`) is not a closed deal — it is not listed here, so the
    pipeline report, the won-cycle median and every closed-deals consumer
    drop it in one move."""
    ws = Path(workspace_root)
    try:
        data = _load_entities(ws)
    except (OSError, json.JSONDecodeError):
        return []
    reversed_won = won_reversals(ws)
    out: list[dict] = []
    for t in _threads(data):
        if not isinstance(t, dict) or t.get("kind") != "deal":
            continue
        deal = t.get("deal") if isinstance(t.get("deal"), dict) else None
        if not deal or deal.get("outcome") not in DEAL_OUTCOMES:
            continue
        if deal.get("outcome") == "won" and t.get("id") in reversed_won:
            continue  # CUTB item 4 — the win was put back by an undo
        out.append({
            "thread_id": t.get("id"),
            "name": t.get("canonical_name") or t.get("display_name") or t.get("id"),
            "org_id": t.get("affiliation_id") or t.get("org_id"),
            "status": t.get("status"),
            "deal": deal,
            "untracked": False,
        })
    return out


def load_deal_events(workspace_root) -> tuple[list[dict], list[dict]]:
    """All deal_* events, via the canonical defensive reader. Returns
    (events, skipped) — the caller MUST surface a non-empty skipped list
    (banner rule), never swallow it."""
    from cru_match import load_events_defensively
    p = _events_path(Path(workspace_root))
    if not p.exists():
        return [], []
    events, skipped = load_events_defensively(p)
    deal_types = {"deal_created", "deal_updated", "deal_stage_changed",
                  "deal_won", "deal_lost",
                  # CUTB item 4 — the won-reversal marker rides along so
                  # `pipeline_math.won_rate_90d` can fold it.
                  "deal_won_reversed"}
    return [e for e in events if e.get("type") in deal_types], skipped


__all__ = [
    "DealStateError",
    "DEAL_STAGES",
    "DEAL_LOSS_REASONS",
    "DEAL_OUTCOMES",
    "DEAL_FORECAST_CATEGORIES",
    "ALLOWED_DEAL_FIELDS",
    "UPDATABLE_DEAL_FIELDS",
    "org_deal_coverage",
    "create_deal",
    "adopt_deal",
    "update_deal",
    "set_stage",
    "close_deal",
    "win_org_without_deal",
    "list_open_deals",
    "list_closed_deals",
    "won_reversals",
    "load_deal_events",
]
