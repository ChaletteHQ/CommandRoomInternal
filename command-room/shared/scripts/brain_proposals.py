#!/usr/bin/env python3
"""Living Brain proposals — the ONE writer API + ONE projector (SPEC LB1, D1).

WHY THIS EXISTS
Eight bespoke proposal/confirm flows already exist and none share rails —
every new detector today adds a ninth selector, render, and resolution
semantics. This module is the shared rails: background detectors write
uncertain observations through `propose()`; surfaces read ONE normalized
queue through `load_open_proposals()` / `select_confirm_card()`; resolutions
land through `resolve_proposal()` (tombstone + shared cooldown ledger).

DOCTRINE (D1–D3, D10 + M rulings R1/R2)
  - **Events + projectors, not a new store.** Proposals are `brain_proposal`
    events through `event_gate.append_event`; the projector folds
    resolution/expiry tombstones. No proposals.json.
  - **Adapters are permanent fossil readers.** LB2 (2026-07) migrated the
    org / project / dormancy / schedule_add WRITERS onto `propose()`; the
    person and commitment_review families still write their legacy types
    (deferred to LB3 — person machinery is PID1-fresh, commitment_review is
    wired into the commitment_state single-closer). The adapters are never
    deleted: historical events are append-only and live on every client
    substrate forever, so pre-migration rows keep rendering here until they
    resolve or expire naturally. No backfill, no event rewriting, ever.
  - **AUTO LIFECYCLE CONTRACT (LB2, the FB-20 mandate):** a detector that
    calls `propose(tier="auto")` MUST, in the same run, apply the change
    through its class's single writer and `resolve_proposal(..., "applied")`
    — an auto proposal never rests open. Auto items are applied-then-
    narrated (the change feed's job), never adjudicated: the projector
    excludes them by default (`include_auto=False`) so no adjudication
    surface can render or count one. `resting_auto_proposals()` is the
    violation detector; a non-empty return is a detector bug, not user
    input.
  - **Policy tiers are code (D2).** `tier="auto"` is legal ONLY for change
    classes in `AUTO_ALLOWED` with a reverser registered in
    `brain_undo.REVERSERS`; `propose()` raises otherwise. Identity- and
    money-shaped changes are always `confirm` (Bug #92 / PIPE1 D9) — except
    the one R1 class: person/org creation from a STRUCTURED CONNECTOR FACT
    (full name + address from mail/calendar attendee, zero same-name/email
    collision via `people_writer.list_same_name_people` /
    `org_writer.find_existing_org`, past the noise gate) is `auto`,
    additive-only, archive-reversible, narrated. Prose-inferred identities
    stay `confirm`; merges stay `confirm` permanently. (The R1 detector
    SHIPPED in PID1 — `identity_reconcile.py` is it; auto clusters apply via
    `people_writer.auto_add_person`, never through this queue.)
  - **Anti-fatigue is contract (D10):** `DAILY_CONFIRM_CAP = 5`; max
    `MAX_SLOTS_PER_DETECTOR = 2` per render; TTL default 14d, silent expiry;
    declined ⇒ 60d fingerprint cooldown via the SHARED `proposal_ledger`
    (`pass` = detector name); a proposal whose action_tuples map to no
    registered verb is rejected at source.
  - **Cross-surface dedup (R2):** a proposal rendered on one daily surface
    today is not re-shown on another daily surface the same day. Shown
    markers live here (`_hq/.system/brain_card_shown.json` — render state,
    not substrate). The Staff Meeting full-set surface and explicit asks
    (system-health) are DELIBERATELY EXEMPT.

All writes go through event_gate / atomic_write. stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# The daily card renders at most this many items (D3). The weekly insights
# widget keeps its own GLOBAL_PROPOSAL_CAP = 7 (unchanged — learning-loop
# proposals ride the weekly widget, never this card).
DAILY_CONFIRM_CAP = 5
DEFAULT_TTL_DAYS = 14
ORG_PROJECT_STALE_DAYS = 30  # review F2 — org/project adapter window
# FS-17 — TTL on LOW-CONTEXT person proposals (name-only single mentions: no
# inferred role AND no inferred org). Mirrors ORG_PROJECT_STALE_DAYS: an aged
# name-only mention must not pollute the queue forever (68 of the 78 live
# staff-meeting rows were exactly this backlog). Rich-context proposals keep
# the F-46 never-expire posture — a real identity decision never dies with
# the chat that captured it.
PERSON_LOW_CONTEXT_STALE_DAYS = 30
MAX_SLOTS_PER_DETECTOR = 2

# LB2 §3a — families whose WRITERS migrated onto propose(). Their proposals
# carry the family's ADAPTER fingerprint convention (the natural key), so
# cross-rail dedup is a fingerprint match against the full projection:
#   org          org:<normalized name>        (was org_proposal events)
#   project      project:<normalized name>    (was project_proposal events)
#   dormancy     dont_forget:<target id>      (was dont_forget_dormant_proposal)
#   schedule_add schedule:<task id>           (schedule_proposals.log_proposal)
# An open PRE-migration row of the same family is adapter-read, not bp — so
# propose() for these kinds also checks the adapter-read side of the
# projection before emitting (the cross-rail dedup trap: without it a
# migrated writer re-proposes something already open as a legacy row and the
# queue shows both). person / commitment_review are NOT here (LB3).
MIGRATED_KINDS: dict[str, str] = {
    "org": "org:",
    "project": "project:",
    "dormancy": "dont_forget:",
    "schedule_add": "schedule:",
    # config_drift is bp-native (no legacy rail) but listed for the
    # fingerprint-convention check: config_drift:<skill>:<knob>.
    "config_drift": "config_drift:",
}

# Change classes legal on the auto tier (D2 — a class table, not a
# confidence score; each also requires brain_undo.has_reverser()).
AUTO_ALLOWED: dict[str, str] = {
    "commitment_close": "HIGH sent-mail evidence — the shipped reconcile-sent "
                        "precedent",
    # R1 (M ruling 2026-07-14): structured-connector-fact identity creation
    # only — additive, archive-reversible, narrated. Detector shipped in
    # PID1 (identity_reconcile.py; applies via auto_add_person).
    "person_org_creation_structured_fact": "identity from a structured "
        "connector fact (full name + address from mail/calendar), zero "
        "same-name/email collision, past the noise gate — additive only",
    # HIST1 Part 2 (D3/S1/S2): one atomic NON-identity fact from a STRUCTURED
    # connector source — an additive *_fact_observed event, reversed by
    # appending entity_fact_retracted (facts are append-only; nothing to
    # flip). Category limited to AUTO_FACT_CATEGORIES; role/company_news
    # stay confirm even from a structured source. Applied via
    # entity_signal_detector.apply_structured_facts (the PID1
    # applied-then-narrated posture — never an open auto proposal, so the
    # FB-20 auto-tier parity gap stays dormant until LB2's gate lands).
    "entity_fact_structured": "one atomic non-identity fact (preference/"
        "contact/personal) from a structured connector source — additive "
        "event, retraction-reversible",
}

# The ONLY categories legal on the entity_fact_structured auto class
# (SPEC HIST1 S2) — mirrors people_writer/org_writer.AUTO_FACT_CATEGORIES
# (writer-level enforcement is code-deep there; this copy guards any future
# propose-path caller). The auto-tier test pins the three copies equal.
AUTO_FACT_CATEGORIES = frozenset({"preference", "contact", "personal"})

# Ranking shapes (D3): money > identity > hygiene, then age.
# org_money (HIST1 Part 2 step 12 — an observed account/contract value on a
# client org; confirm-only forever per D4/Bug #92) ranks money with the
# deal kinds: a value signal that goes silent has the same price tag.
_MONEY_KINDS = frozenset({"deal_update", "deal_creation", "org_money"})
_IDENTITY_KINDS = frozenset({
    "person", "person_update", "org", "project",
    "person_org_creation_structured_fact",
    # PID1 D4 — the reconciler's merge-propose rows: person_link (an open
    # proposal that is already on file — link it?) and person_merge (two
    # EXISTING records that look like one person). Both confirm-only;
    # person_merge is NEVER in AUTO_ALLOWED (merge_person_into has no
    # reverser — a record merge cannot be undone).
    "person_link", "person_merge",
})
# HIST1 Part 2 N3: `entity_fact_structured` and the confirm-tier
# `entity_fact` kind deliberately rank HYGIENE via kind_shape's fall-through
# (below money/identity) — a fact observation is additive context, never a
# record mutation. `person_update` (role/company-move proposals) is already
# in _IDENTITY_KINDS above. Pinned by the auto-tier test.
_SHAPE_RANK = {"money": 0, "identity": 1, "hygiene": 2}

# Daily surfaces the R2 cross-surface dedup applies to. staff-meeting and
# system-health (explicit ask) are exempt by omission; weekly-recap consumes
# roll-up counts, not the card.
DAILY_DEDUP_SURFACES = frozenset({"morning-brief", "coach"})

# The overflow line teaches the full-queue phrase (R3.4). Rendered verbatim
# by surfaces when overflow_count > 0.
OVERFLOW_LINE = "{n} more queued — say `staff meeting` to review everything."


class BrainProposalError(ValueError):
    """Illegal propose() call — wrong tier, missing reverser, unregistered
    verb, malformed tuples. Loud by design: a detector bug, not user input."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value) -> Optional[datetime]:
    from event_time import parse_ts

    return parse_ts(value)


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _load_events(workspace_root) -> list[dict]:
    # SPEC PGUARD1 D1 — the staff-meeting load seam reads ORG-SCOPED: the
    # account mask (filter_masked_events) + personal-lane drop apply by
    # design, so a masked account's history can never drive a proposal card.
    # Shard-transparent + defensive like the prior event_refs-based read.
    # Every consumer in this module (projector, resolve, expiry, health
    # counts) flows through this one seam — keep edits here minimal and
    # localized (PID1 builds against this file in parallel).
    try:
        from events_io import load_events_org_scoped
    except ImportError:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).resolve().parent))
        from events_io import load_events_org_scoped

    path = _events_path(workspace_root)
    if not path.exists():
        return []
    events, _skipped = load_events_org_scoped(workspace_root)
    return events


def kind_shape(kind: str) -> str:
    if kind in _MONEY_KINDS:
        return "money"
    if kind in _IDENTITY_KINDS:
        return "identity"
    return "hygiene"


# ---------------------------------------------------------------------------
# propose() — the single entry point for every NEW detector
# ---------------------------------------------------------------------------

def _validate_action_tuples(action_tuples: list) -> None:
    """D10 — no-consumer proposals rejected at source: every tuple's action
    must be a registered verb_taxonomy action id (the same set the renderer
    validates against)."""
    from verb_taxonomy import CANONICAL_ACTION_IDS

    if not isinstance(action_tuples, list) or not action_tuples:
        raise BrainProposalError(
            "action_tuples must be a non-empty list — a proposal with no "
            "consumer action never enters the card (D10)")
    for t in action_tuples:
        if not isinstance(t, dict) or not t.get("action"):
            raise BrainProposalError(f"malformed action tuple: {t!r}")
        if t["action"] not in CANONICAL_ACTION_IDS:
            raise BrainProposalError(
                f"action {t['action']!r} is not a registered verb_taxonomy "
                "action id — register the verb before proposing through it "
                "(D10 no-consumer rejection)")


def _open_brain_proposals(events: list[dict], *, now: Optional[datetime] = None) -> list[dict]:
    """brain_proposal events minus resolution/expiry tombstones minus
    computed-TTL expiry. Returns normalized dicts (see load_open_proposals)."""
    tombstoned: set[str] = set()
    for ev in events:
        if ev.get("type") in ("brain_proposal_resolved", "brain_proposal_expired"):
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            pid = data.get("proposal_id")
            if pid:
                tombstoned.add(pid)
    out: list[dict] = []
    for ev in events:
        if ev.get("type") != "brain_proposal":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        pid = data.get("proposal_id")
        if not pid or pid in tombstoned:
            continue
        opened = _parse_ts(ev.get("ts"))
        ttl = data.get("ttl_days")
        ttl = int(ttl) if isinstance(ttl, (int, float)) else DEFAULT_TTL_DAYS
        expires = (opened + timedelta(days=ttl)) if opened else None
        if now is not None and expires is not None and expires < now:
            continue  # stale — excluded from render even before the sweep
        kind = data.get("kind") or "unknown"
        out.append({
            "id": pid,
            "source_family": "brain",
            "kind": kind,
            "shape": kind_shape(kind),
            "tier": data.get("tier") or "confirm",
            "fingerprint": data.get("fingerprint") or "",
            "evidence": data.get("evidence") or "",
            "action_tuples": data.get("action_tuples") or [],
            "render_line": data.get("render_line") or "",
            "opened_at": ev.get("ts") or "",
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ") if expires else "",
            "detector": data.get("detector") or "unknown",
            "seq": ev.get("seq"),
            "thread_id": data.get("thread_id"),
            "org_id": data.get("org_id"),
            "person_id": data.get("person_id"),
            # FS-10 — the row TITLE (deal/org/person display name) so the
            # Staff Meeting builds "Name — badge · evidence · consequence".
            "title": data.get("title") or data.get("org_name") or "",
            "proposal_kind": data.get("proposal_kind") or "",
            # LB2 — optional surface routing: a row whose writer named a
            # surface renders ONLY there (config_drift → staff meeting; a
            # config nudge is never urgent). Empty = every surface.
            "surface_hint": data.get("surface_hint") or "",
            # PID1 — merge-propose row payloads (D4): the target ids the
            # apply-choices handlers dispatch on, embedded VERBATIM (F2).
            **{k: data[k] for k in ("cluster_seqs", "cluster_fingerprints",
                                    "keep_id", "duplicate_id", "alias_name",
                                    "matched_name") if data.get(k)},
        })
    return out


def propose(
    workspace_root,
    *,
    kind: str,
    fingerprint: str,
    evidence: str,
    action_tuples: list,
    tier: str,
    ttl_days: int = DEFAULT_TTL_DAYS,
    detector: str,
    change_class: Optional[str] = None,
    render_line: str = "",
    thread_id: Optional[str] = None,
    org_id: Optional[str] = None,
    person_id: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Emit ONE brain_proposal through the gate — the single entry point for
    every new detector. Returns {status, proposal_id?, event?}:
      - "proposed"             — emitted.
      - "suppressed_cooldown"  — declined within 60d (shared ledger); nothing
                                 written.
      - "duplicate_open"       — an open proposal already carries this
                                 fingerprint; nothing written.
    Raises BrainProposalError on illegal calls (auto tier without an
    AUTO_ALLOWED class + registered reverser; unregistered verbs)."""
    if tier not in ("auto", "confirm"):
        raise BrainProposalError(f"tier must be auto|confirm, got {tier!r}")
    if tier == "auto":
        import brain_undo

        if change_class not in AUTO_ALLOWED:
            raise BrainProposalError(
                f"change class {change_class!r} is not in AUTO_ALLOWED — "
                "auto-apply is a class table, not a judgment call (D2)")
        if not brain_undo.has_reverser(change_class):
            raise BrainProposalError(
                f"change class {change_class!r} has no registered reverser "
                "in brain_undo.REVERSERS — auto-apply without a reverser is "
                "illegal (D2/D5)")
        if change_class == "entity_fact_structured":
            # HIST1 S2 guard for any future propose-path caller (the
            # shipped applier, entity_signal_detector.apply_structured_facts,
            # applies directly and never proposes auto — FB-20 parity).
            cat = (extra or {}).get("category")
            if cat not in AUTO_FACT_CATEGORIES:
                raise BrainProposalError(
                    f"category {cat!r} is not auto-eligible — "
                    "entity_fact_structured is limited to "
                    f"{sorted(AUTO_FACT_CATEGORIES)} (S2); role/"
                    "company_news facts stay confirm")
    if not fingerprint or not detector:
        raise BrainProposalError("fingerprint and detector are required")
    if kind in MIGRATED_KINDS and not fingerprint.startswith(MIGRATED_KINDS[kind]):
        raise BrainProposalError(
            f"kind {kind!r} is a migrated family — its fingerprint must carry "
            f"the family natural-key convention {MIGRATED_KINDS[kind]!r} "
            f"(got {fingerprint!r}); cross-rail dedup against pre-migration "
            "adapter rows keys on it (LB2 §3a)")
    _validate_action_tuples(action_tuples)

    from proposal_ledger import active_cooldowns

    now_iso = _now_iso()
    if fingerprint in active_cooldowns(workspace_root, detector, now_iso=now_iso):
        return {"status": "suppressed_cooldown", "fingerprint": fingerprint}

    events = _load_events(workspace_root)
    now = _parse_ts(now_iso)
    open_fps = {p["fingerprint"] for p in _open_brain_proposals(events, now=now)}
    if fingerprint in open_fps:
        return {"status": "duplicate_open", "fingerprint": fingerprint}
    # LB2 cross-rail dedup: for migrated kinds, an open PRE-migration row of
    # the same family (adapter-read, same natural-key fingerprint) also
    # suppresses — the queue must never show a legacy row and a bp row for
    # the same thing. Full projection incl. autos; defensive (a broken
    # adapter never blocks a propose — the bp-side fingerprint check above
    # already ran).
    if kind in MIGRATED_KINDS:
        try:
            legacy_fps = {
                p.get("fingerprint")
                for p in load_open_proposals(
                    workspace_root, now_iso=now_iso, include_auto=True)
                if p.get("source_family") != "brain"
            }
            if fingerprint in legacy_fps:
                return {"status": "duplicate_open_legacy",
                        "fingerprint": fingerprint}
        except Exception:
            pass

    proposal_id = "bp_" + hashlib.sha256(
        f"{fingerprint}|{now_iso}".encode("utf-8")).hexdigest()[:12]
    data = {
        "proposal_id": proposal_id,
        "kind": kind,
        "fingerprint": fingerprint,
        "tier": tier,
        "evidence": (evidence or "")[:400],
        "action_tuples": action_tuples,
        "ttl_days": int(ttl_days),
        "detector": detector,
    }
    if render_line:
        data["render_line"] = render_line
    for key, val in (("thread_id", thread_id), ("org_id", org_id),
                     ("person_id", person_id)):
        if val:
            data[key] = val
    if isinstance(extra, dict):
        for k, v in extra.items():
            data.setdefault(k, v)

    from event_gate import append_event

    events_path = _events_path(workspace_root)
    to_append = [{
        "type": "brain_proposal",
        "source_skill": detector,
        "primary_thread_id": thread_id,
        "data": data,
    }]
    # Deal-kind proposals also write the reserved PIPE1 type alongside for
    # the consumers PIPE1 already named (pipeline-tracker, cleanup). Only
    # deal_update — its payload contract requires a thread_id; creation
    # proposals have no thread yet and ride the generic type alone.
    if kind == "deal_update" and thread_id:
        legacy = {
            "thread_id": thread_id,
            "proposal_kind": (extra or {}).get("proposal_kind") or "update",
            "evidence": (evidence or "")[:400],
            "fingerprint": fingerprint,
        }
        for k in ("proposed_stage", "proposed_value"):
            if (extra or {}).get(k) is not None:
                legacy[k] = extra[k]
        to_append.append({
            "type": "deal_update_proposed",
            "source_skill": detector,
            "primary_thread_id": thread_id,
            "data": legacy,
        })
    append_event(events_path, to_append, holder="brain_proposals")
    return {"status": "proposed", "proposal_id": proposal_id, "data": data}


# ---------------------------------------------------------------------------
# Legacy-family adapters — PERMANENT FOSSIL READERS (fossil-readers posture).
# LB2 migrated the org/project/dormancy/schedule_add writers onto propose();
# these adapters keep rendering PRE-migration rows (append-only history lives
# on every client substrate forever) until they resolve or expire naturally.
# person + commitment_review are still legacy-WRITTEN (LB3) — their adapters
# are the live read path, not fossils. Never delete an adapter.
# ---------------------------------------------------------------------------

# FB-19 — the hygiene review row's registered wire verbs. These are the
# `kind: commitment_review` handlers apply-choices already dispatches
# (`confirm` → commitment_state.close_commitment; `not relevant` →
# build_commitment_review_dismissed_event, the commitment STAYS OPEN), plus
# FB-19's `hold`. Before FB-19 this list was literally `[]` with a comment
# claiming the surface would supply verbs per-kind — no surface ever did, so
# the row rendered with no way to answer it.
_CRU_ACTIONS = [{"action": "confirm"},
                {"action": "not relevant"},
                {"action": "hold"}]


def _cru_commitment_titles(workspace_root) -> dict:
    """commitment_id -> title, for review proposals written before FB-19
    started persisting the title on the event (legacy back-compat: the row
    must be answerable on an EXISTING workspace, not just a fresh one — every
    review proposal already in a live queue predates the writer fix).

    NOTE the shape: `load_open_commitments` returns raw EVENT dicts, so the
    id/title live under `data`, not at the top level. (Reading them off the
    top level returns an empty map and silently drops every legacy row — it
    looks like the drop-empty rule working correctly, which is what makes it
    worth naming here.)"""
    try:
        from cru_match import load_open_commitments
        out = {}
        for ev in load_open_commitments(_events_path(workspace_root)):
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            cid = d.get("id")
            if cid:
                out[cid] = (d.get("title") or "").strip()
        return out
    except Exception:
        return {}


def _cru_render_line(title: str, evidence: str) -> str:
    """FB-19: the row's ASK, in the user's language.

    The live 2026-07-16 render was "Housekeeping — matched your sent message
    X": no name, no question, no verbs — a row that told the user something
    had happened without saying what, and offered no way to respond. This
    composes the question the row is actually asking.

    HONESTY: this row is a PROPOSAL to close (the 0.30-0.55 ambiguous band).
    It has NOT closed anything — anything confident enough to close already
    closed silently and never reaches a card. So the copy asks; it never
    reports ("I closed X — right?" would claim an action that did not
    happen, and offering to "undo" it would compound the lie).
    """
    ask = "Did you already handle this?"
    ev = (evidence or "").strip()
    return f"{ask} Command Room {ev} — close it?" if ev else f"{ask} — close it?"


def _adapt_commitment_reviews(workspace_root) -> list[dict]:
    from cru_match import load_open_review_proposals

    now = _parse_ts(_now_iso())
    titles = None
    out = []
    for ev in load_open_review_proposals(_events_path(workspace_root)):
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        cid = data.get("commitment_id")
        if not cid:
            continue
        # FS-11: an un-adjudicated review proposal older than its TTL expires
        # instead of accumulating (default TTL only when the writer stamped one;
        # legacy proposals with no ttl_days keep the prior forever-open behavior).
        ttl = data.get("ttl_days")
        if isinstance(ttl, (int, float)) and not isinstance(ttl, bool):
            opened = _parse_ts(ev.get("ts"))
            if opened is not None and now is not None and \
                    opened + timedelta(days=int(ttl)) < now:
                continue
        # FB-19 — the row MUST be able to name what it is asking about. The
        # title rides the event since FB-19; legacy events get it from the
        # open-commitment projection (loaded lazily — only when one needs it).
        title = (data.get("title") or "").strip()
        if not title:
            if titles is None:
                titles = _cru_commitment_titles(workspace_root)
            title = titles.get(cid, "")
        evidence = data.get("evidence") or "matched an outbound send"
        if not title:
            # DROP-EMPTY (FB-19): no title means no honest ask — the row would
            # render as a bare "Housekeeping" shrug, which is the defect this
            # fix exists to kill. A row that cannot state its ask does not
            # render at all. The commitment stays open and reachable; only
            # this un-askable prompt is suppressed.
            continue
        out.append({
            "id": f"cru:{cid}",
            "source_family": "commitment_review",
            "kind": "commitment_review",
            "shape": "hygiene",
            "tier": "confirm",
            "fingerprint": f"cru:{cid}",
            "title": title,
            "evidence": evidence,
            "action_tuples": list(_CRU_ACTIONS),
            "render_line": _cru_render_line(title, evidence),
            "opened_at": ev.get("ts") or "",
            "expires_at": "",
            "detector": "reconcile-sent",
            "seq": ev.get("seq"),
            "commitment_id": cid,
            "match_score": data.get("match_score"),
        })
    return out


_MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _short_date(ts: str) -> str:
    """"Jul 8" from an ISO ts — empty string when unparseable (a dated
    evidence phrase NEVER invents a date, the deal-detector rule)."""
    ts = str(ts or "")
    if len(ts) >= 10 and ts[4] == "-" and ts[7] == "-":
        try:
            return f"{_MONTH_ABBR[int(ts[5:7])]} {int(ts[8:10])}"
        except (ValueError, IndexError):
            return ""
    return ""


def person_proposal_is_low_context(p: dict) -> bool:
    """FS-17 — a name-only single mention: no inferred role AND no inferred
    org (nothing to decide on but the bare name). Shared with the backlog
    sweep so the adapter TTL and the sweep's expire branch can never fork."""
    return not (p.get("inferred_role") or p.get("inferred_org"))


def person_proposal_already_on_file(workspace_root, p: dict) -> bool:
    """FS-19 — is this "add person" proposal already satisfied by an existing
    contact? The person queue's missing symmetry with the org adapter
    (`_adapt_org_project_proposals` drops a proposal once `find_existing_org`
    finds the org — "already created, the proposal was actioned"). People had
    the age-out logic but never got the already-exists logic, so anyone
    already on file re-surfaced as a fresh "add" forever — rich-context rows
    never age out (F-46), and "adding" someone who already exists returns
    needs_confirm which LEAVES the proposal open (person_backlog_sweep). That
    is the every-week resurfacing M reported.

    Only ADD-type proposals (`person_proposal`). A `person_update_proposal`
    references an existing person on purpose (a new role/email on someone you
    already have) — those must keep surfacing; existence is their premise, not
    a reason to drop them.

    THE predicate lives in `confirm_flow.person_name_on_file` (confident
    matches only: full-name/email → True; lone first-name Tier-3 ambiguity →
    False, the Bug #19 discipline; fail-open on error). This wrapper adds the
    add-type gate. The queue adapter no longer calls this directly — it reads
    `load_open_person_proposals(..., suppress_on_file=True)` so the filter is a
    single chokepoint shared with the morning brief and the commitments chat.
    Kept as public API + a direct-unit-test seam."""
    if p.get("type") != "person_proposal":
        return False
    from confirm_flow import person_name_on_file

    return person_name_on_file(workspace_root, p.get("name"))


def _person_render_line(p: dict) -> str:
    """FS-17 — the enriched identity row: `{badge} · {source-ref-with-date} ·
    {consequence}`, the same shape the deal rows carry. Provenance-honest:
    the source noun comes from the proposal's own source_ref/evidence text,
    the date from its captured ts — never guessed, dropped when absent."""
    role = (p.get("inferred_role") or "").strip()
    org = (p.get("inferred_org") or "").strip()
    if role and org:
        badge = f"looks like {role} at {org}"
    elif org:
        badge = f"looks connected to {org}"
    elif role:
        badge = f"looks like {role}"
    else:
        badge = "mentioned by name only"
    src_text = f"{p.get('source_ref') or ''} {p.get('evidence') or ''}".lower()
    if any(t in src_text for t in ("granola", "transcript", "meeting")):
        noun = "your meeting notes"
    elif any(t in src_text for t in ("mail", "inbox", "thread", "@", "sent")):
        noun = "an email thread"
    elif "slack" in src_text:
        noun = "a Slack message"
    else:
        noun = "a captured note"
    date = _short_date(p.get("captured_ts"))
    evid = f"surfaced in {noun}" + (f" on {date}" if date else "")
    return f"{badge} · {evid} · no contact record yet"


def _person_row_title(p: dict, person_names: Optional[dict] = None) -> str:
    """FB-8 — the row NAME is load-bearing ("{name — badge · evidence ·
    consequence}"). The as-heard name when the proposal carried one (the
    confirm_flow reader coalesces the legacy field spellings). PID1 title
    fix: an update-type row carrying a `person_id` titles as the RECORD's
    canonical name + " — update" (`person_names` is the adapter's id→name
    map) — the live defect was 4 update rows rendering raw `granola:<uuid>`
    strings. Fallback is a short evidence/review_reason snippet ONLY; a raw
    source_ref is never a title. A nameless ADD row no longer reaches this
    function at all (the adapter drops it — annotation tier, D5)."""
    pid = p.get("person_id")
    if pid and person_names and person_names.get(pid):
        return f"{person_names[pid]} — update"
    name = (p.get("name") or "").strip()
    if name:
        if p.get("type") == "person_update_proposal":
            return f"{name} — update"
        return name
    for key in ("evidence", "review_reason"):
        txt = str(p.get(key) or "").strip()
        if txt:
            return txt if len(txt) <= 60 else txt[:57].rstrip() + "…"
    return ""


def _cluster_render_line(cluster: dict) -> str:
    """D3 — the identity-clustered row's evidence line: the FS-17 enriched
    shape for a single mention, prefixed "seen N× — " with the newest
    source phrases when the cluster merged multiple proposals. Provenance-
    honest exactly like `_person_render_line`: nouns/dates come from each
    proposal's own captured text, dropped when absent."""
    rows = cluster.get("rows") or []
    best = dict(rows[0]) if rows else {}
    best["name"] = cluster.get("name")
    best["inferred_role"] = cluster.get("inferred_role")
    best["inferred_org"] = cluster.get("inferred_org")
    if len(rows) <= 1:
        return _person_render_line(best)
    base = _person_render_line(best)
    # base = "{badge} · surfaced in {noun}[ on {date}] · no contact record
    # yet" — swap the middle segment for the multi-mention phrase.
    parts = base.split(" · ")
    seen = []
    for r in rows[:3]:  # newest-first, capped (T2.2 density)
        src = _person_render_line({**r, "name": cluster.get("name")})
        mid = src.split(" · ")[1] if src.count(" · ") >= 2 else ""
        mid = mid.replace("surfaced in ", "")
        if mid and mid not in seen:
            seen.append(mid)
    middle = f"seen {len(rows)}× — " + ", ".join(seen) if seen \
        else f"seen {len(rows)}×"
    if len(parts) >= 3:
        return " · ".join([parts[0], middle, parts[-1]])
    return f"{middle} · no contact record yet"


_PERSON_ROW_ACTIONS = [
    # Registered verbs (D10 discipline extends to adapters): `add person`
    # and `proposal not relevant` dispatch per the W4b confirm-flow
    # handlers — on a CLUSTER row they fan out across data.cluster_seqs
    # (PID1 D3: one click adjudicates every underlying proposal); `snooze
    # proposal 7d` is the shared snooze. `same as [existing]` stays
    # available through chat / the commitments confirm section — three
    # verbs keep the row dropdown lean (T2.2 density).
    {"action": "add person"},
    {"action": "proposal not relevant"},
    {"action": "snooze proposal 7d"},
]


def _person_id_names(workspace_root) -> dict:
    """person_id -> canonical_name, for the PID1 update-row title fix.
    Defensive: an unreadable entities.json means no map (snippet fallback)."""
    try:
        ent_path = Path(workspace_root) / "_hq" / "data" / "entities.json"
        data = json.loads(ent_path.read_text(encoding="utf-8"))
        ent = data.get("entities") if isinstance(data.get("entities"), dict) \
            else data
        out = {}
        for p in ent.get("people") or []:
            if p.get("id") and p.get("canonical_name"):
                out[p["id"]] = p["canonical_name"]
        return out
    except Exception:
        return {}


def _adapt_person_proposals(workspace_root, events: list[dict],
                            *, now: Optional[datetime] = None) -> list[dict]:
    """FS-17 enrichment + PID1 D3 identity clustering: ONE row per person.

    Add-type proposals group by normalized name (`identity_reconcile.
    person_queue_view` — the SAME projection the morning-brief pointer
    counts, so the two can never disagree); the row carries the cluster's
    best name as title, merged newest-first evidence, and
    `data.cluster_seqs` so apply-choices fans one click across every
    underlying proposal. Nameless add rows NEVER render (annotation tier,
    D5 — an undecidable row); update-type rows for an on-file person render
    separately (existence is their premise) with the record's canonical
    name as title (never a raw source_ref). Low-context single mentions
    still age out after PERSON_LOW_CONTEXT_STALE_DAYS. Auto-eligible
    clusters render as ordinary confirm rows until the Sunday reconciler
    applies them — never render-and-also-auto-apply.

    MERGE-WATCH (PGUARD1): this function's edits are adapter/clustering
    only — the events-LOAD seam (`load_open_person_proposals(_events_path(
    ...))`) is untouched by design."""
    from confirm_flow import load_open_person_proposals
    from identity_reconcile import person_queue_view
    from mute_ledger import active_dismissal_target_ids

    dismissed = active_dismissal_target_ids(events, _now_iso())
    # FS-19 — already a contact? `suppress_on_file=True` drops an "add person"
    # row whose name confidently resolves to an existing record (the org
    # adapter's find_existing_org symmetry), at the SHARED loader so the brief
    # and commitments chat filter identically. Filter-only, like the org path
    # — existence recomputes each render, no tombstone (the reconciler's
    # merge-propose lane is what RESOLVES these; the filter stays render-side
    # truth per the proposal-adapter existence gotcha).
    rows = load_open_person_proposals(_events_path(workspace_root),
                                      dismissed_target_ids=dismissed,
                                      suppress_on_file=True)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ") if now is not None else None
    view = person_queue_view(rows, now_iso=now_iso)

    out = []
    for cluster in view["clusters"]:
        newest = cluster["rows"][0] if cluster["rows"] else {}
        out.append({
            "id": cluster["row_id"],
            "source_family": "person",
            "kind": "person",
            "shape": "identity",
            "tier": "confirm",
            "fingerprint": cluster["row_id"],
            "evidence": newest.get("evidence") or newest.get("review_reason")
                        or "",
            "action_tuples": list(_PERSON_ROW_ACTIONS),
            "render_line": _cluster_render_line(cluster),
            "opened_at": min((r.get("captured_ts") or "")
                             for r in cluster["rows"]) if cluster["rows"]
                         else "",
            "expires_at": "",
            "detector": "confirm-flow",
            "seq": cluster["seqs"][0] if cluster["seqs"] else None,
            "name": cluster["name"],
            # FS-17 / FB-8 — the row TITLE is the person's best as-heard
            # spelling (build_card_view renders `title`).
            "title": cluster["name"],
            "person_id": None,
            "inferred_role": cluster.get("inferred_role"),
            "inferred_org": cluster.get("inferred_org"),
            # PID1 D3 — every underlying proposal id VERBATIM so one click
            # adjudicates the whole cluster (fingerprints carry the D8
            # seq-less rows).
            "cluster_seqs": list(cluster["seqs"]),
            "cluster_fingerprints": list(cluster["fingerprints"]),
        })

    person_names = _person_id_names(workspace_root) if view["updates"] else {}
    for p in view["updates"]:
        out.append({
            "id": f"person:{p.get('seq')}",
            "source_family": "person",
            "kind": "person",
            "shape": "identity",
            "tier": "confirm",
            "fingerprint": f"person:{p.get('seq')}",
            "evidence": p.get("evidence") or p.get("review_reason") or "",
            "action_tuples": list(_PERSON_ROW_ACTIONS),
            "render_line": _person_render_line(p),
            "opened_at": p.get("captured_ts") or "",
            "expires_at": "",
            "detector": "confirm-flow",
            "seq": p.get("seq"),
            "name": p.get("name"),
            "title": _person_row_title(p, person_names),
            "person_id": p.get("person_id"),
            "inferred_role": p.get("inferred_role"),
            "inferred_org": p.get("inferred_org"),
        })
    return out


def _adapt_org_project_proposals(workspace_root, events: list[dict],
                                 *, now: Optional[datetime] = None) -> list[dict]:
    """org_proposal / project_proposal are prose-written (no shared selector)
    — read defensively. Natural tombstones: an org_proposal_declined naming
    the same org, or the entity already existing (the proposal was actioned).
    Staleness window (review F2, mirrors _adapt_dont_forget): proposals older
    than ORG_PROJECT_STALE_DAYS never surface — months-old zombie prose
    proposals must not pollute the card on a mature workspace."""
    from org_writer import find_existing_org

    declined_names: set[str] = set()
    for ev in events:
        if ev.get("type") != "org_proposal_declined":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        name = (data.get("name") or data.get("org_name") or "").strip().lower()
        if name:
            declined_names.add(name)

    threads_names: set[str] = set()
    try:
        ent_path = Path(workspace_root) / "_hq" / "data" / "entities.json"
        ent = json.loads(ent_path.read_text(encoding="utf-8"))
        ent = ent.get("entities") if isinstance(ent.get("entities"), dict) else ent
        for t in (ent.get("threads") or ent.get("projects") or []):
            nm = (t.get("display_name") or t.get("name") or "").strip().lower()
            if nm:
                threads_names.add(nm)
    except Exception:
        pass

    out: list[dict] = []
    seen: set[str] = set()
    for ev in events:
        etype = ev.get("type")
        if etype not in ("org_proposal", "project_proposal"):
            continue
        if now is not None:
            opened = _parse_ts(ev.get("ts"))
            if opened is not None and (now - opened).days > ORG_PROJECT_STALE_DAYS:
                continue  # zombie prose proposal — never surfaces (F2)
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        name = (data.get("name") or data.get("org_name")
                or data.get("project_name") or data.get("title") or "").strip()
        if not name:
            continue
        key = f"{etype}:{name.lower()}"
        if key in seen:
            continue
        seen.add(key)
        if etype == "org_proposal":
            if name.lower() in declined_names:
                continue
            try:
                if find_existing_org(workspace_root, name=name):
                    continue  # already created — the proposal was actioned
            except Exception:
                pass
            kind, family = "org", "org"
        else:
            if name.lower() in threads_names:
                continue  # thread exists — actioned
            kind, family = "project", "project"
        out.append({
            "id": f"{family}:{ev.get('seq')}",
            "source_family": family,
            "kind": kind,
            "shape": "identity",
            "tier": "confirm",
            "fingerprint": f"{family}:{name.lower()}",
            "evidence": data.get("evidence") or data.get("reason") or "",
            "action_tuples": [],
            "render_line": "",
            "opened_at": ev.get("ts") or "",
            "expires_at": "",
            "detector": "confirm-flow",
            "seq": ev.get("seq"),
            "name": name,
            # FB-8 — build_card_view renders `title`; an org/project identity
            # row without one renders the nameless shape fallback.
            "title": name,
        })
    return out


def _adapt_dont_forget(workspace_root, events: list[dict],
                       *, now: Optional[datetime]) -> list[dict]:
    """Open dormancy-transition proposals (prose-written by the dont-forget
    orchestrator). A proposal is retired by a later decline (14d cooldown),
    snooze, or status_change on the same target."""
    latest: dict[str, dict] = {}

    def _target(data: dict) -> str:
        for k in ("thread_id", "project_id", "person_id", "target_id", "target"):
            v = data.get(k)
            if isinstance(v, str) and v:
                return v
        return ""

    retired: set[str] = set()
    for ev in events:
        etype = ev.get("type")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        tgt = _target(data)
        if not tgt:
            continue
        if etype == "dont_forget_dormant_proposal":
            latest[tgt] = ev
        elif etype in ("dont_forget_dormant_proposal_declined",
                       "dont_forget_snooze", "status_change"):
            retired.add(tgt)
    out = []
    for tgt, ev in latest.items():
        if tgt in retired:
            continue
        opened = _parse_ts(ev.get("ts"))
        if now is not None and opened is not None and (now - opened).days > 30:
            continue  # stale prose proposals age out of the queue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        out.append({
            "id": f"dont_forget:{ev.get('seq')}",
            "source_family": "dont_forget",
            "kind": "dormancy",
            "shape": "hygiene",
            "tier": "confirm",
            "fingerprint": f"dont_forget:{tgt}",
            "evidence": data.get("reason") or data.get("evidence") or "",
            "action_tuples": [],
            "render_line": "",
            "opened_at": ev.get("ts") or "",
            "expires_at": "",
            "detector": "dont-forget",
            "seq": ev.get("seq"),
            "target_id": tgt,
        })
    return out


def _adapt_schedule_add(workspace_root, registered_task_ids) -> list[dict]:
    """Later-add schedule proposals — computed live (schedule_proposals owns
    thresholds + 6-week suppression). Only when the caller can supply the
    registered set (it comes from the scheduler MCP, not the substrate)."""
    if registered_task_ids is None:
        return []
    from schedule_proposals import propose_later_add_tasks

    out = []
    for p in propose_later_add_tasks(workspace_root, registered_task_ids):
        out.append({
            "id": f"schedule:{p['task']}",
            "source_family": "schedule_add",
            "kind": "schedule_add",
            "shape": "hygiene",
            "tier": "confirm",
            "fingerprint": f"schedule:{p['task']}",
            "evidence": p.get("reason") or "",
            "action_tuples": [],
            "render_line": p.get("line") or "",
            "opened_at": "",
            "expires_at": "",
            "detector": "schedule-proposals",
            "seq": None,
            "task": p["task"],
        })
    return out


# ---------------------------------------------------------------------------
# Projector + card selection
# ---------------------------------------------------------------------------

def _shown_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / ".system" / "brain_card_shown.json"


def _load_shown(workspace_root) -> dict:
    path = _shown_path(workspace_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def mark_shown(workspace_root, proposal_ids, surface: str,
               *, now_iso: Optional[str] = None) -> None:
    """R2 shown-markers: record that these ids rendered on `surface` today.
    Render state, not substrate — lives under _hq/.system/. Never raises."""
    try:
        from atomic_write import atomic_write_json

        now = now_iso or _now_iso()
        day = now[:10]
        shown = _load_shown(workspace_root)
        # prune other days so the file never grows
        shown = {k: v for k, v in shown.items()
                 if isinstance(v, dict) and v.get("date") == day}
        for pid in proposal_ids:
            shown[pid] = {"date": day, "surface": surface}
        path = _shown_path(workspace_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, shown)
    except Exception:
        pass


def load_open_proposals(
    workspace_root,
    surface: Optional[str] = None,
    *,
    now_iso: Optional[str] = None,
    registered_task_ids=None,
    include_legacy: bool = True,
    include_auto: bool = False,
) -> List[dict]:
    """THE projector (D1): one normalized open-proposal queue — generic
    brain_proposal events (minus tombstones/TTL) PLUS adapter reads over the
    legacy families. Each item: {id, source_family, kind, shape, tier,
    fingerprint, evidence, action_tuples, render_line, opened_at,
    expires_at, detector, seq, ...family extras}.

    **The queue contains CONFIRM-tier items only by default** (LB2 parity
    fix, the FB-20 mandate): auto-tier proposals are applied-then-narrated,
    never adjudicated — they are not "things that need your eyes", so no
    surface may render or count one. `include_auto=True` is the explicit
    escape for diagnostic consumers (the parity test, resting-auto checks)
    — never for an adjudication surface.

    `surface` drives the R2 cross-surface dedup: on a DAILY_DEDUP_SURFACES
    surface, items already shown TODAY on a DIFFERENT surface are dropped.
    staff-meeting / system-health / None see the full set (deliberate
    exemption — the full-queue surfaces and explicit asks)."""
    now_iso = now_iso or _now_iso()
    now = _parse_ts(now_iso)
    events = _load_events(workspace_root)
    items = _open_brain_proposals(events, now=now)
    if include_legacy:
        items += _adapt_commitment_reviews(workspace_root)
        items += _adapt_person_proposals(workspace_root, events, now=now)
        items += _adapt_org_project_proposals(workspace_root, events, now=now)
        items += _adapt_dont_forget(workspace_root, events, now=now)
        items += _adapt_schedule_add(workspace_root, registered_task_ids)
    # Uniform snooze/decline gate (review F1): a chat_dismissal whose
    # target_id is a projector item id retires that item for the
    # dismissal's TTL — this is what `snooze proposal 7d` and the
    # project-row `not relevant` write. The person adapter pre-filters
    # internally (same ledger); double-filtering is harmless.
    try:
        from mute_ledger import active_dismissal_target_ids
        dismissed = active_dismissal_target_ids(events, now_iso)
        if dismissed:
            items = [i for i in items if i["id"] not in dismissed]
    except Exception:
        pass
    # LB2 parity default — auto-tier items leave the projection HERE, so
    # every consumer (staff meeting, brief pointer, system-health counts,
    # schedule readiness) inherits the filter in one move, future call
    # sites included. Adapters only emit confirm-tier rows, so this bites
    # bp rows alone.
    if not include_auto:
        items = [i for i in items if i.get("tier") != "auto"]
    if surface in DAILY_DEDUP_SURFACES:
        day = now_iso[:10]
        shown = _load_shown(workspace_root)
        items = [
            i for i in items
            if not (
                isinstance(shown.get(i["id"]), dict)
                and shown[i["id"]].get("date") == day
                and shown[i["id"]].get("surface") != surface
            )
        ]
    return items


# FB-20 — the money carve-out's prose, one sentence per kind. The brief is
# read-only now, so these sentences carry NO verbs and NO row: they name the
# signal and point at the surface that adjudicates it. Chat-phrase
# adjudication ("say staff meeting"), propose-only, never silent.
_MONEY_PROSE = {
    "deal_creation": "Command Room thinks {name} is a live deal — say "
                     "`staff meeting` to confirm.",
    "deal_update": "Command Room thinks the {name} deal moved — say "
                   "`staff meeting` to confirm.",
    # HIST1 Part 2 (step 12) — the org account-value lane rides the same
    # carve-out: propose-only prose, adjudication at the staff meeting.
    "org_money": "Command Room spotted an account value for {name} — say "
                 "`staff meeting` to confirm.",
}


def money_prose_lines(items: List[dict], *, cap: int = 3) -> List[str]:
    """FB-20: the money-class items from a proposal queue, as one prose
    sentence each (ranked, capped, drop-empty).

    THE ONE EXCEPTION to the read-only brief. Every other class the brief
    used to card is now reachable only via the pointer line; money is named
    outright because a deal signal that goes silent for a day is the one
    failure with a price tag. Propose-only by construction: prose carries no
    action tuples, so nothing here can be applied from the brief — the
    sentence's own copy routes the user to the staff meeting.

    `cap` bounds the render, not the truth: the caller's pointer count
    includes every capped item. Returns [] when no money is open — the brief
    then says nothing about deals (drop-empty; never an all-clear line)."""
    out: List[str] = []
    for item in rank_proposals([i for i in items
                                if kind_shape(i.get("kind") or "") == "money"]):
        if len(out) >= cap:
            break
        template = _MONEY_PROSE.get(item.get("kind"))
        if not template:
            continue  # an unmapped money kind gets no invented sentence
        name = (item.get("title") or "").strip()
        if not name:
            continue  # never "Command Room thinks  is a live deal"
        out.append(template.format(name=name))
    return out


def rank_proposals(items: List[dict]) -> List[dict]:
    """D3 ordering: money > identity > hygiene, then age (oldest first;
    undated items last within their shape)."""
    def _key(item):
        shape = _SHAPE_RANK.get(item.get("shape"), 2)
        dt = _parse_ts(item.get("opened_at"))
        age_key = dt.timestamp() if dt else float("inf")
        return (shape, age_key, str(item.get("id")))
    return sorted(items, key=_key)


def select_confirm_card(
    workspace_root,
    surface: str,
    *,
    now_iso: Optional[str] = None,
    registered_task_ids=None,
    cap: int = DAILY_CONFIRM_CAP,
) -> dict:
    """The "Needs your eyes" card selector (D3/D10): ranked, capped at
    `cap`, max MAX_SLOTS_PER_DETECTOR per detector. Returns
    {items, overflow_count, total_open, overflow_line}. On daily surfaces
    the returned items are marked shown (R2)."""
    now_iso = now_iso or _now_iso()
    open_items = load_open_proposals(
        workspace_root, surface, now_iso=now_iso,
        registered_task_ids=registered_task_ids)
    # Review F5: auto-tier proposals never enter the confirm card — an
    # auto change is applied-then-narrated (the change feed's job), not
    # queued for consent. Redundant since LB2 flipped the projector default
    # (include_auto=False) — kept as defense-in-depth.
    open_items = [i for i in open_items if i.get("tier") != "auto"]
    # LB2 — surface routing: a row whose writer named a surface renders only
    # there (config_drift → staff meeting). The daily card drops hinted rows
    # for other surfaces; load_open_proposals callers (the staff meeting)
    # see the full set, hint included.
    open_items = [i for i in open_items
                  if not i.get("surface_hint")
                  or i["surface_hint"] == surface]
    ranked = rank_proposals(open_items)
    picked: list[dict] = []
    per_detector: dict[str, int] = {}
    for item in ranked:
        if len(picked) >= cap:
            break
        det = item.get("detector") or "unknown"
        if per_detector.get(det, 0) >= MAX_SLOTS_PER_DETECTOR:
            continue
        per_detector[det] = per_detector.get(det, 0) + 1
        picked.append(item)
    overflow = max(0, len(open_items) - len(picked))
    if surface in DAILY_DEDUP_SURFACES and picked:
        mark_shown(workspace_root, [i["id"] for i in picked], surface,
                   now_iso=now_iso)
    return {
        "items": picked,
        "overflow_count": overflow,
        "total_open": len(open_items),
        "overflow_line": OVERFLOW_LINE.format(n=overflow) if overflow else "",
    }


# ---------------------------------------------------------------------------
# Card / queue VIEW builder (FS-09 / FS-10 — mechanize the row + section shape
# so the runtime renders the queue instead of improvising opaque clusters).
# ---------------------------------------------------------------------------

_SHAPE_SECTION_LABEL = {"money": "MONEY", "identity": "IDENTITY",
                        "hygiene": "HYGIENE"}
_SHAPE_TILE_LABEL = {"money": "Money", "identity": "Identity",
                     "hygiene": "Hygiene"}
_SHAPE_NAME_FALLBACK = {"money": "Deal signal", "identity": "Needs confirming",
                        "hygiene": "Housekeeping"}


def _row_actions(item: dict) -> list:
    """Registered wire verbs for a row — the action ids from the proposal's
    own action_tuples (validated at propose() time via D10). NEVER an invented
    bulk verb; the display label is the verb taxonomy's job at render time."""
    out = []
    for t in item.get("action_tuples") or []:
        act = t.get("action") if isinstance(t, dict) else None
        if act:
            out.append(act)
    return out


def _row_name(item: dict) -> str:
    """The row header — the deal/org/person display name (title), never an id.
    Falls back to a shape label only when no title was stored."""
    title = (item.get("title") or "").strip()
    if title:
        return title
    return _SHAPE_NAME_FALLBACK.get(item.get("shape"), "Needs your eyes")


def _row_target_ids(item: dict) -> dict:
    """The F2 identity rule for card rows: embed every underlying target id
    VERBATIM alongside the proposal id so apply-choices dispatches exactly.
    PID1: cluster rows also embed cluster_seqs / cluster_fingerprints (one
    click adjudicates the whole cluster) and person_link/person_merge rows
    embed their record ids."""
    data = {"id": item["id"]}
    for k in ("thread_id", "org_id", "person_id", "cluster_seqs",
              "cluster_fingerprints", "keep_id", "duplicate_id"):
        if item.get(k):
            data[k] = item[k]
    return data


def build_card_view(
    items: List[dict],
    *,
    surface: str = "staff-meeting",
    header: Optional[str] = None,
    extra_sections: Optional[list] = None,
) -> dict:
    """Build the ready-to-render widget data view for the Living Brain card /
    Staff Meeting queue from a RANKED proposal list (FS-09 / FS-10).

    Groups the queue into money > identity > hygiene sections, each titled with
    its HONEST count, and builds each row as
    `{name — badge · evidence-with-date · consequence}` carrying ONLY the
    proposal's registered verbs (no invented bulk verbs). Header tiles show the
    per-shape counts. `extra_sections` (e.g. the Staff Meeting "This week's
    moves" rows) append after the queue sections.

    Returns a dict suitable for `render_and_persist(data_view=...)` /
    `render_chat_output_widget` — `source_skill` is "cr-brain" so the renderer
    stamps `src` for stateless apply-choices dispatch.
    """
    by_shape: dict[str, list] = {"money": [], "identity": [], "hygiene": []}
    for it in items:
        by_shape.setdefault(it.get("shape", "hygiene"), []).append(it)

    sections: list[dict] = []
    display_n = 0  # T2.2 (RV-5): sequential VISIBLE number; `n` stays the wire id
    for shape in ("money", "identity", "hygiene"):
        rows_in = by_shape.get(shape) or []
        if not rows_in:
            continue
        rows: list[dict] = []
        for it in rows_in:
            display_n += 1
            row = {
                "n": it["id"],                       # id embedded verbatim (F2)
                "display_n": display_n,              # what the row SHOWS (RV-5)
                "name": _row_name(it),
                # `context_tag` is the renderer's per-row context line — it
                # renders as "{name} — {context_tag}", giving M's agreed shape
                # "Acme Co — likely deal · proposal language in your
                # Jul 8 sent mail · no pipeline record" (render_line verbatim,
                # Bug #92b).
                "context_tag": it.get("render_line") or it.get("evidence") or "",
                "data": _row_target_ids(it),
                "actions": _row_actions(it),
            }
            rows.append(row)
        sections.append({
            "title": f"{_SHAPE_SECTION_LABEL[shape]} ({len(rows_in)})",
            "items": rows,
        })

    # Drop-empty BEFORE rendering — the tile component refuses a 0-value tile
    # (an empty frame is never data); a shape with no rows just doesn't tile.
    tiles = [
        {"label": _SHAPE_TILE_LABEL[s], "value": len(by_shape.get(s) or [])}
        for s in ("money", "identity", "hygiene")
        if by_shape.get(s)
    ]

    # RV-4 off-by-one: the header count must equal the ROWS THE WIDGET SHOWS —
    # queue rows PLUS extra-section rows (the moves row was narrated but not
    # counted). Extra rows also continue the visible numbering.
    extras = list(extra_sections or [])
    n_extra = 0
    for sec in extras:
        for it in sec.get("items", []) or []:
            n_extra += 1
            if "display_n" not in it:
                it["display_n"] = display_n + n_extra
    total = len(items) + n_extra
    if header is None:
        header = (f"Staff Meeting — {total} waiting on you"
                  if surface == "staff-meeting"
                  else f"Needs your eyes — {total} open")
    view = {
        "source_skill": "cr-brain",
        "header": header,
        "tiles": tiles,
        "sections": sections + extras,
    }
    return view


# ---------------------------------------------------------------------------
# Resolution + expiry
# ---------------------------------------------------------------------------

def resolve_proposal(
    workspace_root,
    proposal_id: str,
    user_action: str,
    *,
    resolved_by: str,
    source_skill: str,
    note: str = "",
) -> dict:
    """Adjudicate ONE brain-family proposal (bp_*): append the
    brain_proposal_resolved tombstone AND the shared-ledger decision row
    (pass = detector) so cooldown math is shared with the learning loops.
    Legacy-family items resolve through their existing flows — this function
    refuses non-bp ids loudly. Idempotent over already-resolved ids.

    `user_action` values: applied / edited / declined / skipped, plus
    `superseded` (T2.2 review F-4) — the proposal's premise no longer holds
    (e.g. a deal_creation whose org is already covered per
    deal_state.org_deal_coverage), retired by the SYSTEM on a user click that
    was NOT a decline. Same tombstone + the same 60d cooldown as declined
    (covered items must not re-propose), but the audit/ledger row tells the
    truth about what happened."""
    if user_action not in ("applied", "edited", "declined", "skipped",
                           "superseded"):
        raise BrainProposalError(
            f"user_action must be applied|edited|declined|skipped|superseded, "
            f"got {user_action!r}")
    if not proposal_id.startswith("bp_"):
        raise BrainProposalError(
            f"{proposal_id!r} is not a brain-family proposal id — pre-"
            "migration legacy items resolve through their own shipped flows "
            "(the adapters are permanent fossil readers; new writes for the "
            "migrated families arrive as bp_ rows and resolve here)")
    events = _load_events(workspace_root)
    match = None
    for item in _open_brain_proposals(events, now=None):
        if item["id"] == proposal_id:
            match = item
            break
    if match is None:
        return {"status": "already_resolved", "proposal_id": proposal_id}

    from event_gate import append_event
    from proposal_ledger import append_decision

    to_append = [{
        "type": "brain_proposal_resolved",
        "source_skill": source_skill,
        "data": {
            "proposal_id": proposal_id,
            "user_action": user_action,
            "kind": match["kind"],
            "fingerprint": match["fingerprint"],
            "detector": match["detector"],
            "resolved_by": resolved_by,
            "note": (note or "")[:200],
        },
    }]
    # Declined/superseded deal-kind proposals also write the reserved PIPE1
    # dismissal type for its named consumers (detector cooldown check,
    # usage-report).
    if user_action in ("declined", "superseded") and match["kind"] in _MONEY_KINDS \
            and match.get("thread_id"):
        to_append.append({
            "type": "deal_update_dismissed",
            "source_skill": source_skill,
            "primary_thread_id": match.get("thread_id"),
            "data": {
                "thread_id": match["thread_id"],
                "fingerprint": match["fingerprint"],
                "reason": (note or "declined on the confirm card")[:200],
            },
        })
    append_event(_events_path(workspace_root), to_append,
                 holder="brain_proposals")
    append_decision(
        workspace_root,
        pass_name=match["detector"],
        fingerprint=match["fingerprint"],
        user_action=user_action,
        summary=match.get("evidence") or "",
    )
    return {"status": "resolved", "proposal_id": proposal_id,
            "user_action": user_action, "kind": match["kind"]}


def expire_stale(
    workspace_root,
    *,
    now_iso: Optional[str] = None,
    source_skill: str = "cleanup",
) -> dict:
    """The cleanup expiry sweep (D3/D10): tombstone every open brain-family
    proposal past its TTL with a silent brain_proposal_expired — logged,
    never nagged. Returns {n_expired, expired}."""
    now_iso = now_iso or _now_iso()
    now = _parse_ts(now_iso)
    events = _load_events(workspace_root)
    live = _open_brain_proposals(events, now=None)         # tombstone-filtered
    fresh_ids = {i["id"] for i in _open_brain_proposals(events, now=now)}
    stale = [i for i in live if i["id"] not in fresh_ids]
    if not stale:
        return {"n_expired": 0, "expired": []}

    from event_gate import append_event

    append_event(_events_path(workspace_root), [{
        "type": "brain_proposal_expired",
        "source_skill": source_skill,
        "data": {
            "proposal_id": i["id"],
            "kind": i["kind"],
            "fingerprint": i["fingerprint"],
            "detector": i["detector"],
            "opened_ts": i["opened_at"],
        },
    } for i in stale], holder="brain_proposals")
    return {"n_expired": len(stale), "expired": [i["id"] for i in stale]}


def resting_auto_proposals(
    workspace_root,
    *,
    now_iso: Optional[str] = None,
) -> List[dict]:
    """AUTO LIFECYCLE CONTRACT violation detector (LB2 §3c, the FB-20
    mandate): open auto-tier bp rows. By contract this list is ALWAYS empty —
    a detector that proposes auto must apply + `resolve_proposal(...,
    "applied")` in the same run, so an open auto proposal is a detector bug
    (it would be invisible on every adjudication surface yet still expire
    into the rot ledger — the latent honesty gap §2d names). Diagnostic
    consumer: passes include_auto=True explicitly."""
    now_iso = now_iso or _now_iso()
    events = _load_events(workspace_root)
    return [i for i in _open_brain_proposals(events, now=_parse_ts(now_iso))
            if i.get("tier") == "auto"]


def card_health_counts(
    workspace_root,
    *,
    now_iso: Optional[str] = None,
    window_days: int = 30,
) -> dict:
    """Cleanup Monday-note card health (D10): open count + how many expired
    unseen in the window — rot made visible. `resting_auto` (LB2) counts
    open auto-tier rows — contract violations (see resting_auto_proposals);
    always 0 on a healthy workspace. `open` counts CONFIRM-tier items only
    (what actually waits on the user — parity with every surface)."""
    now_iso = now_iso or _now_iso()
    now = _parse_ts(now_iso)
    events = _load_events(workspace_root)
    open_items = _open_brain_proposals(events, now=now)
    n_resting_auto = sum(1 for i in open_items if i.get("tier") == "auto")
    n_open = len(open_items) - n_resting_auto
    n_expired = 0
    for ev in events:
        if ev.get("type") != "brain_proposal_expired":
            continue
        dt = _parse_ts(ev.get("ts"))
        if dt is not None and now is not None \
                and (now - dt).days <= window_days:
            n_expired += 1
    return {"open": n_open, "expired_in_window": n_expired,
            "resting_auto": n_resting_auto}


__all__ = [
    "DAILY_CONFIRM_CAP",
    "DEFAULT_TTL_DAYS",
    "MAX_SLOTS_PER_DETECTOR",
    "AUTO_ALLOWED",
    "DAILY_DEDUP_SURFACES",
    "OVERFLOW_LINE",
    "BrainProposalError",
    "kind_shape",
    "person_proposal_is_low_context",
    "person_proposal_already_on_file",
    "PERSON_LOW_CONTEXT_STALE_DAYS",
    "MIGRATED_KINDS",
    "propose",
    "load_open_proposals",
    "resting_auto_proposals",
    "rank_proposals",
    "select_confirm_card",
    "build_card_view",
    "resolve_proposal",
    "expire_stale",
    "card_health_counts",
    "mark_shown",
]
