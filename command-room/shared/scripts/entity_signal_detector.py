#!/usr/bin/env python3
"""Detect entity signals — role/company moves and discrete facts about
TRACKED people/orgs — and route them onto the Living Brain rails
(SPEC HIST1 Part 2, step 11). Mirrors prospect_conversion_detector /
deal_signal_detector: pure, substrate-only, no connectors, stdlib only.

FENCE (HIST1 vs LB2 vs PID1): HIST1 owns FACT-TIER ENRICHMENT —
observations about entities already on file. PID1 owns identity
creation/dedup (identity_reconcile IS the R1 detector); LB2 owns
proposal-family migration + the propose-rail auto lifecycle. This module
never creates people or orgs, never merges, never proposes identity
creation — an unknown name is meeting-notes/PID1 territory and is simply
not this detector's signal.

TWO LANES
  PROSE lane — detect_entity_signals() / run_entity_signal_scan(): scans
  recent substrate events' text for signals about resolved entities.
  Everything here is tier="confirm" — prose is NEVER auto (D3):
    - promotion/title language near a tracked person → a `person_update`
      proposal (proposal_kind="role_change") when the bounded title lexicon
      extracts a title confidently; otherwise an `entity_fact` proposal
      (category="role") — a fact observation, not a field change.
    - company-move language plus a co-referenced tracked org that differs
      from the person's `primary_org_id` → a `person_update` proposal
      (proposal_kind="org_change"). Confirming applies through
      people_writer.update_person, whose Part 1 hook auto-emits the
      person_org_changed lineage event.
    - org news markers (fundraise, acquisition, launch, leadership hire…)
      → an `entity_fact` proposal (category="company_news").
  STRUCTURED lane — apply_structured_facts(): caller-supplied facts read
  from STRUCTURED connector metadata (a signature block, a calendar field,
  a profile field — never model inference over prose; the CALLER owns that
  distinction and must not launder prose through this lane). Non-identity
  categories (preference/contact/personal) auto-apply through the fact
  writers, batch-stamped so ONE `undo` retracts the batch; identity-
  adjacent categories (role/company_news) DEMOTE to confirm proposals —
  never dropped, never auto (S2).

AUTO LIFECYCLE HONESTY (FB-20 parity): the auto lane never calls
propose(tier="auto") — it is applied-then-narrated (the PID1
identity_reconcile posture). The change feed narrates auto-noted facts
from the WRITTEN events with a standing `undo`; an open auto proposal
would break the brief-count/staff-meeting parity LB2's gate closes.

COOLDOWN / CAPS: confirm proposals ride propose()'s shared ledger cooldown
(60d on decline) + open-fingerprint dedup; per-run caps bound both lanes
(overflow demotes or is reported, never silent).

PRIVACY (PGUARD1 lane doctrine): every event read here is ORG-SCOPED via
events_io.load_events_org_scoped — the mask + personal-lane drop apply by
design, so a masked account's history or a tie:personal contact's events
can never drive a proposal or an auto fact. Consequence: this module simply
never fires on personal-lane entities (their events are dropped upstream);
explicit user facts about them still work through people-crm's Part 1 verb.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Look-back for the prose scan (shorter than the deal detector's 120d —
# entity chatter goes stale faster than deal language).
TEXT_WINDOW_DAYS = 30

# Per-run bounds. Confirm proposals additionally ride DAILY_CONFIRM_CAP /
# MAX_SLOTS_PER_DETECTOR at render time — this cap bounds the WRITE side.
MAX_PROPOSALS_PER_RUN = 5
AUTO_FACT_CAP = 10

_DETECTOR = "entity-signals"

# Promotion / title-change language (lowercased substring match).
PROMOTION_MARKERS = (
    "promoted", "promotion", "new role", "new title", "stepping into",
    "taking over as", "now leads", "now leading", "named the new",
    "appointed",
)
# Company-move language.
MOVE_MARKERS = (
    "joined", "moving to", "moved to", "now at", "left the company",
    "leaving", "departing", "no longer at", "landed at",
)
# Org news (company_news facts — identity-adjacent, always confirm).
ORG_NEWS_MARKERS = (
    "raised a series", "series a", "series b", "series c", "seed round",
    "funding round", "acquired", "acquisition", "merger", "ipo",
    "layoffs", "restructuring", "rebranded", "opened an office",
    "new office", "launched", "hired a new", "new cfo", "new ceo",
    "new coo",
)

# Bounded title lexicon — a role_change proposal carries proposed_role ONLY
# when one of these extracts; anything fuzzier stays a fact observation.
_TITLE_RE = re.compile(
    r"(?:promoted to|now(?: the)?|named|appointed|taking over as)\s+"
    r"((?:chief \w+ officer)|ceo|cfo|coo|cto|cro|cmo|"
    r"(?:vp of [\w ]{2,24})|vp|(?:head of [\w ]{2,24})|"
    r"(?:director of [\w ]{2,24})|president|general manager|"
    r"managing director|partner|principal)\b",
    re.IGNORECASE,
)

# Event types the scan never reads for signals — they are this system's own
# outputs (proposals, facts, lineage, undo markers). Scanning them would
# echo a signal back as a fresh one.
_SELF_REFERENTIAL_TYPES = frozenset({
    "brain_proposal", "brain_proposal_resolved", "brain_proposal_expired",
    "person_fact_observed", "org_fact_observed", "entity_fact_retracted",
    "person_role_changed", "person_org_changed", "brain_change_undone",
    "person_proposal", "person_update_proposal", "person_proposal_resolved",
    "person_proposal_reopened", "deal_update_proposed",
})

_TEXT_FIELDS = ("title", "summary", "notes", "text", "description", "label",
                "name")

CONFIRM_TUPLES = [
    {"action": "confirm proposal"},
    {"action": "dismiss proposal"},
    {"action": "snooze proposal 7d"},
]


def _entities(workspace_root: Path) -> dict:
    p = workspace_root / "_hq" / "data" / "entities.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["entities"] if isinstance(d.get("entities"), dict) else d


def _load_events(workspace_root: Path) -> list[dict]:
    # ORG-SCOPED by design (PGUARD1 D1): mask + personal-lane drop apply, so
    # masked/personal history can never drive a proposal or an auto fact.
    from events_io import load_events_org_scoped

    events, _skipped = load_events_org_scoped(workspace_root)
    return events


def _event_text(ev: dict) -> str:
    parts: list[str] = []
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for src in (ev, d):
        for f in _TEXT_FIELDS:
            v = src.get(f)
            if isinstance(v, str):
                parts.append(v)
    return " ".join(parts).lower()


def _event_persons(ev: dict) -> set[str]:
    """Every person id an event references (the render_person_history
    extraction, kept in lockstep — top-level person_ids[], data.person_id /
    person_ids[], commitment owner/counterparty)."""
    out: set[str] = set()
    top = ev.get("person_ids")
    if isinstance(top, list):
        out.update(x for x in top if isinstance(x, str))
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    if isinstance(data.get("person_id"), str):
        out.add(data["person_id"])
    inner = data.get("person_ids")
    if isinstance(inner, list):
        out.update(x for x in inner if isinstance(x, str))
    for k in ("owner_id", "owner_person_id", "counterparty_id"):
        if isinstance(data.get(k), str):
            out.add(data[k])
    return {p for p in out if p.startswith("person_")}


def _event_org_ids(ev: dict) -> set[str]:
    out: set[str] = set()
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for r in (ev.get("org_ids") or []):
        out.add(r)
    for r in (d.get("org_ids") or []):
        out.add(r)
    for k in ("org_id", "primary_org_id", "to_org_id", "from_org_id"):
        for src in (ev, d):
            v = src.get(k)
            if isinstance(v, str) and v:
                out.add(v)
    return {o for o in out if isinstance(o, str) and o.startswith("org_")}


def _snippet(text: str, marker: str, radius: int = 60) -> str:
    i = text.find(marker)
    if i < 0:
        return marker
    start = max(0, i - radius)
    end = min(len(text), i + len(marker) + radius)
    return ("…" if start else "") + text[start:end].strip() + (
        "…" if end < len(text) else "")


# ---------------------------------------------------------------------------
# PROSE lane — detect (pure) + scan (proposes, tier=confirm ONLY)
# ---------------------------------------------------------------------------

def detect_entity_signals(workspace_root: str | Path) -> list[dict]:
    """Return prose-inferred signal candidates about TRACKED entities:
        {"kind": "person_update"|"entity_fact", "fingerprint", "evidence",
         "render_line", "person_id"|"org_id", "title", extras...}
    Every candidate is confirm-tier by construction — this lane never
    auto-applies anything (D3). Empty list when nothing qualifies."""
    workspace_root = Path(workspace_root)
    ent = _entities(workspace_root)
    people = {p["id"]: p for p in (ent.get("people") or [])
              if p.get("id") and p.get("status") != "archived"}
    orgs = {o["id"]: o for o in (ent.get("orgs") or [])
            if o.get("id") and o.get("status") != "archived"}
    if not people and not orgs:
        return []

    events = _load_events(workspace_root)
    cutoff = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(days=TEXT_WINDOW_DAYS)).strftime("%Y-%m-%d")

    candidates: list[dict] = []
    seen_fp: set[str] = set()

    def _push(cand: dict) -> None:
        if cand["fingerprint"] in seen_fp:
            return
        seen_fp.add(cand["fingerprint"])
        candidates.append(cand)

    for ev in events:
        if ev.get("type") in _SELF_REFERENTIAL_TYPES:
            continue
        ts = str(ev.get("ts") or "")
        if ts and ts[:10] < cutoff:
            continue
        text = _event_text(ev)
        if not text:
            continue
        ev_people = _event_persons(ev) & set(people)
        ev_orgs = _event_org_ids(ev) & set(orgs)

        for pid in ev_people:
            person = people[pid]
            name = person.get("canonical_name") or pid

            promo = next((m for m in PROMOTION_MARKERS if m in text), None)
            if promo:
                m = _TITLE_RE.search(text)
                if m:
                    role = m.group(1).strip()
                    if (person.get("role") or "").lower() != role.lower():
                        _push({
                            "kind": "person_update",
                            "proposal_kind": "role_change",
                            "person_id": pid,
                            "title": name,
                            "proposed_role": role,
                            "evidence": f'"{_snippet(text, promo)}"',
                            "fingerprint": f"es:role:{pid}:{role.lower()}",
                            "render_line": (
                                f"🧠 Sounds like {name}'s role changed to "
                                f"{role} — confirm to update their record "
                                "(the old role is kept as history)."),
                        })
                else:
                    _push({
                        "kind": "entity_fact",
                        "proposal_kind": "fact",
                        "person_id": pid,
                        "title": name,
                        "fact": _snippet(text, promo),
                        "category": "role",
                        "evidence": f'"{_snippet(text, promo)}"',
                        "fingerprint": f"es:pfact:{pid}:{promo}",
                        "render_line": (
                            f"🧠 Role signal about {name}: "
                            f"\"{_snippet(text, promo)}\" — confirm to "
                            "save it to their history."),
                    })

            move = next((m for m in MOVE_MARKERS if m in text), None)
            if move:
                to_org = next(
                    (o for o in ev_orgs
                     if o != person.get("primary_org_id")), None)
                if to_org:
                    org_name = (orgs[to_org].get("canonical_name")
                                or to_org)
                    _push({
                        "kind": "person_update",
                        "proposal_kind": "org_change",
                        "person_id": pid,
                        "title": name,
                        "to_org_id": to_org,
                        "evidence": f'"{_snippet(text, move)}"',
                        "fingerprint": f"es:move:{pid}:{to_org}",
                        "render_line": (
                            f"🧠 Sounds like {name} moved to {org_name} — "
                            "confirm to update their record (the move is "
                            "kept as lineage)."),
                    })

        for oid in ev_orgs:
            news = next((m for m in ORG_NEWS_MARKERS if m in text), None)
            if news:
                org_name = orgs[oid].get("canonical_name") or oid
                _push({
                    "kind": "entity_fact",
                    "proposal_kind": "fact",
                    "org_id": oid,
                    "title": org_name,
                    "fact": _snippet(text, news),
                    "category": "company_news",
                    "evidence": f'"{_snippet(text, news)}"',
                    "fingerprint": f"es:ofact:{oid}:{news}",
                    "render_line": (
                        f"🧠 News signal about {org_name}: "
                        f"\"{_snippet(text, news)}\" — confirm to save it "
                        "to the company history."),
                })

    return candidates


def run_entity_signal_scan(
    workspace_root: str | Path,
    *,
    source_skill: str = _DETECTOR,
) -> dict:
    """Detect → propose each candidate through the Living Brain rail
    (tier="confirm" ONLY; ledger cooldown + open-dedup enforced inside
    propose()). Capped per run; overflow is counted, never silent.
    Returns {n_candidates, n_proposed, n_suppressed, n_capped}."""
    import brain_proposals

    candidates = detect_entity_signals(workspace_root)
    n_proposed = n_suppressed = n_capped = 0
    for c in candidates:
        if n_proposed >= MAX_PROPOSALS_PER_RUN:
            n_capped += 1
            continue
        extra = {"proposal_kind": c["proposal_kind"], "title": c["title"]}
        for k in ("proposed_role", "to_org_id", "fact", "category"):
            if c.get(k) is not None:
                extra[k] = c[k]
        result = brain_proposals.propose(
            workspace_root,
            kind=c["kind"],
            fingerprint=c["fingerprint"],
            evidence=c["evidence"],
            action_tuples=CONFIRM_TUPLES,
            tier="confirm",
            detector=_DETECTOR,
            render_line=c["render_line"],
            org_id=c.get("org_id"),
            person_id=c.get("person_id"),
            extra=extra,
        )
        if result["status"] == "proposed":
            n_proposed += 1
        else:
            n_suppressed += 1
    return {"n_candidates": len(candidates), "n_proposed": n_proposed,
            "n_suppressed": n_suppressed, "n_capped": n_capped}


# ---------------------------------------------------------------------------
# STRUCTURED lane — the ONLY auto path (S2-limited, batch-reversible)
# ---------------------------------------------------------------------------

def _existing_fact_keys(workspace_root: Path) -> set[tuple[str, str]]:
    """(target_id, normalized fact) for every un-retracted fact on file —
    the dedup set (a re-observed fact must not spam the history render).
    Org-scoped like every read here — a personal-lane fact this can't see
    also can't be re-noted, because the lane drop skips those targets
    entirely before the writer is reached."""
    events = _load_events(workspace_root)
    retracted: set[int] = set()
    for ev in events:
        if ev.get("type") != "entity_fact_retracted":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        rs = d.get("retracts_seq")
        if isinstance(rs, int) and not isinstance(rs, bool):
            retracted.add(rs)
    out: set[tuple[str, str]] = set()
    for ev in events:
        if ev.get("type") not in ("person_fact_observed",
                                  "org_fact_observed"):
            continue
        seq = ev.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool) \
                and seq in retracted:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        target = d.get("person_id") or d.get("org_id")
        fact = d.get("fact")
        if isinstance(target, str) and isinstance(fact, str):
            out.add((target, " ".join(fact.lower().split())))
    return out


def apply_structured_facts(
    workspace_root: str | Path,
    facts: list[dict],
    *,
    source_skill: str = _DETECTOR,
) -> dict:
    """Apply STRUCTURED-CONNECTOR facts (SPEC HIST1 Part 2, D3/S1/S2).

    Each fact: {"target_id": person_/org_ id (already ENTITY_RESOLVEd by
    the caller — never a raw name), "fact": str, "category": str,
    "source_ref": str, "confidence"?: str}. The caller vouches that every
    fact came from a STRUCTURED source field — prose inference must go
    through run_entity_signal_scan instead.

    Routing per fact:
      - category in AUTO_FACT_CATEGORIES and under the per-run cap →
        applied via record_person_fact / record_org_fact with
        brain_batch_id + brain_change_class stamps (ONE `undo` retracts
        the whole batch via brain_undo).
      - identity-adjacent category (role/company_news/other/None) OR over
        the auto cap → a tier="confirm" `entity_fact` proposal — demoted,
        never dropped, never auto (S2).
      - already on file un-retracted → skipped (dedup).

    Returns {batch_id, n_auto_applied, n_proposed, n_suppressed,
    n_skipped_dup, n_errors, applied, undo_line}. undo_line is the
    narration sentence surfaces render verbatim (the change feed derives
    the same line independently from the written events — FB-20 CHANGED
    contract)."""
    import brain_proposals
    from org_writer import record_org_fact
    from people_writer import AUTO_FACT_CATEGORIES, record_person_fact

    workspace_root = Path(workspace_root)
    batch_id = "efb_" + _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    existing = _existing_fact_keys(workspace_root)
    applied: list[dict] = []
    n_proposed = n_suppressed = n_skipped = n_errors = 0
    errors: list[dict] = []

    def _demote(f: dict, target: str, why: str) -> None:
        nonlocal n_proposed, n_suppressed
        title = f.get("title") or target
        result = brain_proposals.propose(
            workspace_root,
            kind="entity_fact",
            fingerprint=f"esf:{target}:"
                        f"{' '.join(str(f.get('fact', '')).lower().split())[:80]}",
            evidence=f"structured source — {f.get('source_ref', '')}",
            action_tuples=CONFIRM_TUPLES,
            tier="confirm",
            detector=_DETECTOR,
            render_line=(f"🧠 From a connected source: \"{f.get('fact')}\" "
                         f"({title}) — confirm to save it to history."),
            person_id=target if target.startswith("person_") else None,
            org_id=target if target.startswith("org_") else None,
            extra={"proposal_kind": "fact", "fact": f.get("fact"),
                   "category": f.get("category"), "title": title,
                   "source_ref": f.get("source_ref"), "demoted_why": why},
        )
        if result["status"] == "proposed":
            n_proposed += 1
        else:
            n_suppressed += 1

    for f in facts or []:
        target = str(f.get("target_id") or "")
        fact = str(f.get("fact") or "").strip()
        source_ref = str(f.get("source_ref") or "").strip()
        category = f.get("category")
        if not target or not fact or not source_ref:
            n_errors += 1
            errors.append({"fact": f, "error": "target_id, fact and "
                          "source_ref are all required"})
            continue
        if (target, " ".join(fact.lower().split())) in existing:
            n_skipped += 1
            continue
        auto_eligible = category in AUTO_FACT_CATEGORIES
        if auto_eligible and len(applied) < AUTO_FACT_CAP:
            writer = record_person_fact if target.startswith("person_") \
                else record_org_fact
            try:
                ev = writer(workspace_root, target, fact, source_ref,
                            category=category,
                            confidence=f.get("confidence") or "high",
                            source_skill=source_skill,
                            brain_batch_id=batch_id,
                            brain_change_class="entity_fact_structured")
            except Exception as exc:  # loud per-item, contained per-batch
                n_errors += 1
                errors.append({"fact": f,
                               "error": f"{type(exc).__name__}: {exc}"})
                continue
            existing.add((target, " ".join(fact.lower().split())))
            applied.append({"target_id": target, "fact": fact,
                            "category": category,
                            "seq": ev.get("seq")})
        elif auto_eligible:
            # Over the cap — demoted to confirm, narrated never silent
            # (the PID1 §0-3 spill posture).
            _demote(f, target, "auto cap reached")
        else:
            # Identity-adjacent (or uncategorized) — confirm by law (S2).
            _demote(f, target, "identity-adjacent category stays confirm")

    n = len(applied)
    undo_line = (f"Noted {n} fact{'s' if n != 1 else ''} from your "
                 "connected sources — say `undo` to reverse.") if n else ""
    return {"batch_id": batch_id, "n_auto_applied": n,
            "n_proposed": n_proposed, "n_suppressed": n_suppressed,
            "n_skipped_dup": n_skipped, "n_errors": n_errors,
            "errors": errors, "applied": applied, "undo_line": undo_line}


__all__ = [
    "TEXT_WINDOW_DAYS",
    "MAX_PROPOSALS_PER_RUN",
    "AUTO_FACT_CAP",
    "PROMOTION_MARKERS",
    "MOVE_MARKERS",
    "ORG_NEWS_MARKERS",
    "detect_entity_signals",
    "run_entity_signal_scan",
    "apply_structured_facts",
]


if __name__ == "__main__":
    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    for c in detect_entity_signals(ws):
        print(f"[{c['kind']:13s}] {c.get('title', ''):24s} — {c['evidence']}")
