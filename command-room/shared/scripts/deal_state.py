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
    threads = entities_collection(data, "threads")
    projects = entities_collection(data, "projects")
    if projects and not threads:
        return projects
    return threads


def _find_thread(data: dict, thread_id: str) -> Optional[dict]:
    return next((t for t in _threads(data) if t.get("id") == thread_id), None)


def _find_org(data: dict, org_id: str) -> Optional[dict]:
    return next((o for o in entities_collection(data, "orgs")
                 if o.get("id") == org_id), None)


def _append(ws: Path, event, source_skill: str) -> None:
    """Gated append. Takes one event or a list — a list lands in ONE append, so
    two events written for the same transition cannot half-land."""
    from event_gate import append_event
    events = [event] if isinstance(event, dict) else list(event)
    append_event(_events_path(ws), events, holder=source_skill)


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
) -> dict:
    """Open a new deal: a kind='deal' thread carrying the nested deal object,
    plus a `deal_created` event. The org must already exist (create it via
    workspace-manager's `new prospect` / org_writer first) — a deal always
    hangs off a real org edge.

    `value` is the user's stated number or None — NEVER an estimate; ranges
    are out of v1 scope (store None + a note in `source`).
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
        affiliation_id=org_id,
        owner_person_id=owner_person_id,
        deal=deal,
        source_skill=source_skill,
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
        when the deal's org is a prospect, atomically runs the SAME
        conversion path workspace-manager's "[Name] is now a client" uses —
        org_writer.update_org flip + engagement edge. One utterance, one
        result. Preconditions (a resolvable primary-focus org) are checked
        BEFORE any write so a refused conversion leaves nothing half-done.
        With convert_prospect=False on a prospect-org win, the return
        carries `conversion_suggestion` for the skill to render — the org is
        NOT touched (acceptance §7 item 5). Detector-observed signals never
        set this flag.

    Returns {"status": "closed", "outcome", "thread_id", "org_id",
    "converted": bool, "conversion_suggestion": str|None, "event": {...}}
    or {"status": "already_closed", ...}.
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

    converted = False
    if focus_org is not None:
        # The SAME path workspace-manager's "[Name] is now a client" runs
        # (Bug #91 conversion): typed-writer org flip + engagement edge.
        import engagement_writer
        import org_writer
        flipped = org_writer.update_org(
            ws, org_id, relationship_type="client", source_skill=source_skill)
        assert flipped.get("relationship_type") == "client" and "stage" not in flipped, \
            "conversion failed or wrote a stage field"
        existing = engagement_writer.find_existing_engagement(
            ws, from_org_id=focus_org["id"], to_org_id=org_id)
        if existing:
            engagement_writer.update_engagement(
                ws, existing["id"], label="Active client", is_active=True,
                source_skill=source_skill)
        else:
            engagement_writer.create_engagement(
                ws, from_org_id=focus_org["id"], to_org_id=org_id,
                kind="client", label="Active client",
                inferred_from=["prospect_converted"],
                source_skill=source_skill)
        converted = True
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
    _append(ws, to_append, source_skill)

    suggestion = None
    if outcome == "won" and org_is_prospect and not converted:
        org_name = (org or {}).get("canonical_name") or org_id
        suggestion = f"{org_name} is now a client"

    return {
        "status": "closed",
        "thread_id": thread_id,
        "outcome": outcome,
        "org_id": org_id,
        "converted": converted,
        "conversion_suggestion": suggestion,
        "event": event,
    }


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


def list_closed_deals(workspace_root) -> list[dict]:
    """Closed deals (terminal outcome on the deal object). Same row shape as
    list_open_deals plus the deal carries outcome/closed_at — the won-cycle
    and won-rate inputs pipeline_math reads."""
    ws = Path(workspace_root)
    try:
        data = _load_entities(ws)
    except (OSError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for t in _threads(data):
        if not isinstance(t, dict) or t.get("kind") != "deal":
            continue
        deal = t.get("deal") if isinstance(t.get("deal"), dict) else None
        if not deal or deal.get("outcome") not in DEAL_OUTCOMES:
            continue
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
                  "deal_won", "deal_lost"}
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
    "list_open_deals",
    "list_closed_deals",
    "load_deal_events",
]
