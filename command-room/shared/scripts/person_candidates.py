#!/usr/bin/env python3
"""person_candidates — the person-learning loop (SPEC PERSONLOOP1).

THE PROBLEM THIS EXISTS TO KILL
===============================
A capture whose person the graph cannot resolve is stamped `pending_review`
and waits. Nothing ever proposes creating that person, so the same handful of
names fail resolution meeting after meeting, and every meeting mints more
waiting rows. The graph never learns a person from a meeting, which makes the
failure SELF-SUSTAINING: the pile grows by construction.

The retroactive half already shipped (RRF1 / BUG-8330 item 4): the moment a
person record exists, every row blocked on that name drains through
`review_reasons` -> the projection's `review_reason_auto_satisfied` fold ->
`needs_review_queue.confirm_satisfied_reasons`. What was missing was entirely
upstream — nothing ever asked.

WHAT THIS MODULE IS
===================
  derive_candidates   PURE READ. The recurring unresolved names, live-derived
                      from the open pending queue on every render. NEVER
                      cached to substrate: a candidate is a question about
                      the CURRENT graph, and a stored one would answer with
                      yesterday's graph.
  candidate_rows      those candidates as widget rows (the ONE row shape all
                      three surfaces render — a second row builder would be a
                      second idea of what the question is).
  candidate_section   a `build_card_view(extra_sections=[...])`-shaped
                      section, or None. Drop-empty all the way up.
  telemetry           counts only, for the fire receipt (§3-4).
  resolve_candidate   THE write path for an answer. One call, one gesture:
                      the entity write, then the drain, then the counts.
  load_suppressions   the durable ignore ledger (append-only events).

THREE HARD RULES, AND EACH ONE IS A FENCE
=========================================
1. **PROPOSE ONLY (§0-2).** Nothing on the derivation path writes anything.
   Auto-creating a person from a transcript spelling would have minted a
   misspelling as a real human being — which is the design case this spec was
   written around. `derive_candidates` has no writer imports and
   `resolve_candidate` REFUSES an unnamed action, so a person record can only
   ever appear behind an explicit answer.

2. **RECURRENCE, NOT VOLUME (§0-1).** A name seen once is noise; a name
   failing resolution across two different meetings is a hole in the graph.
   The bar is >= `RECURRENCE_MIN_SOURCE_REFS` DISTINCT source refs — not row
   count, which one chatty meeting can fake on its own.

3. **AN IGNORE SUPPRESSES THE PROPOSAL AND NOTHING ELSE (§3-2).** "Not a
   person" means "stop asking me about this name". It must NEVER mean "stop
   capturing commitments that mention it": the capture is the record of
   something that was said, and suppressing that would silently lose real
   promises to a UI preference. So the suppression ledger is read HERE and
   nowhere else — no capture path imports this module, and the test suite
   pins that a suppressed name still captures.

GROUPING IS EXACT-NORMALIZED ONLY (§0-4). casefold + whitespace collapse, so
`bo sample` / `Bo Sample` are one candidate. No phonetic or fuzzy matching:
two spellings that differ by a letter stay two candidates and the HUMAN
answer merges them by naming the aliases. Fuzzy grouping would guess at
identity, which is the one thing this loop must not do.

stdlib only.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

SOURCE_SKILL = "needs-your-call"

# §0-1 — the recurrence bar. DISTINCT source refs, so one meeting that
# produced nine rows is still one sighting.
RECURRENCE_MIN_SOURCE_REFS = 2

# §0-3 — at most this many proposals per fire, on any surface. The section is
# an offer inside a page that already has a job; a third row makes it a queue.
PROPOSAL_CAP = 2

# The durable ignore ledger's event type (registered in
# shared/data-schemas/events.schema.json + shared/EVENT_TYPES.md). Additive,
# append-only, per name-and-org — the same posture as the
# `person_proposal_resolved` / `not_relevant` tombstone it mirrors, for the
# same reason: an adjudicated question must stop re-surfacing forever, and
# events are the only durable place a per-name decision can live.
SUPPRESSION_EVENT = "person_candidate_suppressed"

# The three answers a candidate row accepts. `resolve_candidate` refuses
# anything else, and refuses an absent action — there is deliberately no
# default, because every default here is a write nobody asked for.
ACTION_ADD = "add person"
ACTION_SAME_AS = "same as [existing]"
ACTION_NOT_A_PERSON = "not a person"
CANDIDATE_ACTIONS = (ACTION_ADD, ACTION_SAME_AS, ACTION_NOT_A_PERSON)

SECTION_TITLE = "PEOPLE THE GRAPH KEEPS MISSING"

# The wire-id prefix for a candidate row. A candidate has no substrate id (it
# is derived, never stored), so its identity is a fingerprint of the
# normalized name — stable across fires, and it keeps a name out of the
# `data-n` attribute.
ROW_ID_PREFIX = "pcand:"

_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def name_key(name) -> str:
    """The §0-4 grouping key: casefold + whitespace collapse. "" for anything
    that is not usable text — an empty key never becomes a candidate."""
    if not isinstance(name, str):
        return ""
    return _WS_RE.sub(" ", name.strip()).casefold()


# Internal alias. `resolve_candidate`'s wire contract names its argument
# `name_key` (it is the key the widget row carries), which shadows the
# function inside that scope — so the normalizer is reached by this name
# there. One alias beats renaming the wire field.
_norm = name_key


def org_key(org_id) -> str:
    """The suppression ledger's org half. "" means "no org named", which is
    the ordinary case and suppresses the name wherever it appears."""
    if not isinstance(org_id, str):
        return ""
    return org_id.strip().lower()


def suppression_key(name, org_id=None) -> str:
    """`<name key>|<org key>` — per-name, per-org (§3-2)."""
    return f"{name_key(name)}|{org_key(org_id)}"


def row_id(key: str) -> str:
    """`pcand:<12 hex>` — the widget wire id for a candidate."""
    digest = hashlib.sha256((key or "").encode("utf-8")).hexdigest()[:12]
    return f"{ROW_ID_PREFIX}{digest}"


# ---------------------------------------------------------------------------
# The suppression ledger (read)
# ---------------------------------------------------------------------------

def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _suppression_key_of(ev) -> str:
    """THE parser of one suppression event -> its `<name key>|<org key>`, or
    "" for anything that is not one.

    Factored out by ATTENDEE1 (review F-1) so the two READING POSTURES below
    cannot drift on what a suppression means. One parser, two postures — the
    difference between them is the cost of doubt, never the vocabulary."""
    if not isinstance(ev, dict) or ev.get("type") != SUPPRESSION_EVENT:
        return ""
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    key = d.get("suppression_key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    nm = d.get("name_key") or d.get("name")
    if isinstance(nm, str) and nm.strip():
        return suppression_key(nm, d.get("org_id"))
    return ""


def load_suppressions(workspace_root) -> set:
    """Every `<name key>|<org key>` the user has said "not a person" to.

    Defensive: an unreadable log yields an EMPTY set, which shows the
    proposal again rather than silently hiding a question the user may never
    have answered. Fail-open is the right direction here — the cost is one
    extra ask, and the alternative is a decision nobody made.

    THAT POSTURE IS RIGHT FOR A QUESTION AND WRONG FOR A WRITE, so an
    automatic rail must NOT use this — see `load_suppressions_checked`
    (ATTENDEE1 review F-1: a suppression on a malformed line silently became
    permission to auto-create the very person the user had set aside)."""
    out: set = set()
    try:
        from events_io import iter_events
    except Exception:  # pragma: no cover — never widen on an import failure
        return out
    try:
        for ev in iter_events(Path(workspace_root)):
            k = _suppression_key_of(ev)
            if k:
                out.add(k)
    except Exception:  # pragma: no cover — same posture
        return out
    return out


def load_suppressions_checked(workspace_root) -> tuple:
    """`(keys, fully_read)` — the posture an AUTOMATIC writer needs.

    WHY THIS EXISTS (ATTENDEE1 review F-1). `load_suppressions` above catches
    `Exception` in both of its blocks and always returns a set, so it never
    raises and never returns None — which made every "unreadable ledger means
    ignored" claim downstream DEAD CODE. Worse, the realistic corruption shape
    never raises at all: `events_io._iter_file` skips unparseable interior
    lines SILENTLY and deliberately (historical junk lines exist and fixtures
    contain some). So a suppression event sitting on a clobbered line simply
    vanished, and the fire read the user's explicit "not a person" as
    permission.

    `fully_read` is the missing bit. It is False when the ledger EXISTS but
    could not be read completely — the file would not open, or ANY line failed
    to parse — measured through `cru_match.load_events_defensively`, whose own
    contract says the caller must surface `skipped` and "DO NOT silently
    swallow". This is that caller.

    AN ABSENT LEDGER IS A CLEAN READ, NOT CORRUPTION, and that distinction is
    deliberate: a workspace with no events.jsonl has nothing suppressed, so
    there is no decision to honour and nothing to protect. Failing closed
    there would silently disable the whole evidence rail on every fresh
    install and buy no safety at all. Corruption is "the file is there and I
    cannot vouch for what it says" — that is the only case that spends a
    user's "no".
    """
    out: set = set()
    try:
        from cru_match import load_events_defensively
    except Exception:  # pragma: no cover — cannot vouch for the ledger
        return out, False
    path = _events_path(workspace_root)
    if not path.exists():
        return out, True          # nothing suppressed, cleanly
    try:
        events, skipped = load_events_defensively(str(path))
    except Exception:
        # An unopenable file (a directory in its place, a permission wall)
        # raises straight out of the reader — it does not guard its own
        # `open`. That is exactly the case we must not read as "empty".
        return out, False
    for ev in events:
        k = _suppression_key_of(ev)
        if k:
            out.add(k)
    return out, not skipped


def is_suppressed(suppressed: Iterable[str], key: str, org_id=None) -> bool:
    """Is this candidate suppressed? An org-less suppression covers the name
    everywhere; an org-scoped one covers only that org's candidate."""
    have = set(suppressed or ())
    return (f"{key}|" in have) or (f"{key}|{org_key(org_id)}" in have)


# ---------------------------------------------------------------------------
# Derivation (PURE READ — this is the propose-only path)
# ---------------------------------------------------------------------------

def _unresolved_names(row) -> list:
    """Every unresolved-person name a pending row's reason points at.

    TWO clause classes, and the second is why this reads the row and not just
    the sentence: `counterparty 'X' has no person record` names X inline,
    while `no resolved owner` is stamped BARE and names its subject on
    `data.owner_external` (capture_gate.py:351)."""
    from review_reasons import (NO_OWNER_CLAUSE, NO_PERSON_RE,
                                owner_external_of)

    d = row.get("data") if isinstance(row.get("data"), dict) else {}
    reason = str(d.get("review_reason") or "")
    names: list = []
    for clause in (c.strip() for c in reason.split(";")):
        if not clause:
            continue
        m = NO_PERSON_RE.match(clause)
        if m:
            names.append(m.group(1).strip())
        elif clause == NO_OWNER_CLAUSE:
            ext = owner_external_of(row)
            if ext:
                names.append(ext)
    return [n for n in names if n]


def _row_org_id(row) -> str:
    """An org this row is attributed to, or "". Read, never inferred: the
    commitment schema carries org attribution only when a writer actually
    resolved one, and on real substrate that is a small minority of rows.
    A proposal's org is therefore PROPOSED from unanimity below, or left
    empty — never guessed from a name."""
    d = row.get("data") if isinstance(row.get("data"), dict) else {}
    for field in ("org_id", "owner_org_id", "primary_org_id"):
        v = d.get(field)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def derive_candidates(workspace_root, *, now_iso: Optional[str] = None,
                      min_source_refs: int = RECURRENCE_MIN_SOURCE_REFS,
                      include_suppressed: bool = False) -> list:
    """The recurring unresolved names, worst-blocked first. PURE READ.

    TWO PASSES, AND THE SPLIT IS THE WHOLE DESIGN (§3-1: "open pending rows
    PLUS recent capture events"). They answer two different questions, and
    collapsing them into one produces nonsense in both directions:

      RECURRENCE comes from the CAPTURE HISTORY — every `commitment` event
      whose capture-time stamp named a person the graph could not place. That
      is the record of the disease: "the graph failed on this name in these
      meetings". It has to be the history, because the queue is drained
      constantly (RRF1 clears sole-clause rows the moment a record appears,
      expiry sweeps age rows out, the user drops them) — so by the time a
      name has failed five times, the evidence of four of those failures is
      no longer in the open queue. Measured against the live workspace this
      spec was written for: the open pending set held 16 distinct unresolved
      names and NONE of them reached two distinct source refs, while the
      capture history held the same recurring names several meetings deep. A
      pending-only derivation is a loop that proposes nothing on the exact
      workspace whose pile it exists to drain.

      URGENCY comes from the OPEN PENDING QUEUE — `cru_match.load_needs_review`,
      the canonical seam, with every adjudication fold already applied.
      `n_rows_blocked` is a claim about work that is stuck RIGHT NOW, and a
      candidate must block at least one row: a name whose captures were all
      answered is not holding anything, and asking about it is a chore with
      no payoff. That requirement is also the recency filter, deliberately
      instead of a time window — an unfloored or hardcoded window is a
      constant that silently widens (and a stuck row IS the honest signal
      that this name still matters).

    Each candidate:
      {"key", "row_id", "canonical_guess", "observed_spellings",
       "n_rows_blocked", "n_meetings", "source_refs", "org_id",
       "commitment_ids"}

    `canonical_guess` is the most-observed spelling — a GUESS, and the answer
    path exists to correct it. `observed_spellings` is what the answer turns
    into aliases, which is how a transcript misspelling stops costing
    anything forever.

    NOTHING HERE WRITES. No writer is imported, no event is appended, no
    record is created. That is the §0-2 propose-only fence, and the suite
    pins it by asserting a full derivation pass leaves the substrate
    byte-identical.
    """
    from cru_match import _commitment_id, _commitment_field, load_needs_review
    from review_reasons import _resolves_to_person

    ws = Path(workspace_root)

    def _group(key: str) -> dict:
        return groups.setdefault(key, {
            "key": key, "spellings": {}, "refs": set(),
            "commitment_ids": [], "orgs": set(), "blocked": False,
        })

    groups: dict = {}

    # PASS 1 — recurrence, off the capture history. The capture event's OWN
    # stamp is read (not the projection's), because the as-heard spelling and
    # the meeting it was heard in are properties of the capture.
    try:
        from events_io import iter_events

        for ev in iter_events(ws):
            if not isinstance(ev, dict) or ev.get("type") != "commitment":
                continue
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            ref = str(d.get("source_ref") or "").strip()
            for raw in _unresolved_names(ev):
                key = name_key(raw)
                if not key:
                    continue
                g = _group(key)
                g["spellings"][raw] = g["spellings"].get(raw, 0) + 1
                if ref:
                    g["refs"].add(ref)
    except Exception:  # pragma: no cover — a broken log proposes nothing
        return []

    # PASS 2 — urgency, off the open pending projection.
    rows = load_needs_review(str(_events_path(ws)), workspace_root=str(ws))
    for row in rows:
        if not isinstance(row, dict):
            continue
        ref = str(_commitment_field(row, "source_ref") or "").strip()
        cid = _commitment_id(row)
        org = _row_org_id(row)
        for raw in _unresolved_names(row):
            key = name_key(raw)
            if not key:
                continue
            g = _group(key)
            # Pass 1 already counted every capture, so a spelling it saw is
            # NOT counted twice here (double-weighting the blocked rows would
            # let the projection outvote the history on `canonical_guess`).
            # A spelling only the PROJECTION carries — a fold that rewrote the
            # reason — still registers, at weight one.
            g["spellings"].setdefault(raw, 1)
            if ref:
                g["refs"].add(ref)
            if cid and cid not in g["commitment_ids"]:
                g["commitment_ids"].append(cid)
            g["orgs"].add(org)
            g["blocked"] = True

    suppressed = set() if include_suppressed else load_suppressions(ws)
    resolve_cache: dict = {}
    out: list = []
    for key, g in groups.items():
        # Something must actually be stuck (pass 2) AND the graph must have
        # failed on this name in at least `min_source_refs` meetings (pass 1).
        if not g["blocked"]:
            continue
        if len(g["refs"]) < max(1, int(min_source_refs)):
            continue
        spellings = sorted(g["spellings"],
                           key=lambda s: (-g["spellings"][s], -len(s), s))
        # The name may have gained a record since the rows were stamped (the
        # RRF1 drain is per-row, and a multi-clause row keeps its question);
        # a name that resolves today is not a hole in the graph.
        if any(_resolves_to_person(ws, s, resolve_cache) for s in spellings):
            continue
        # §0-2 — the org is PROPOSED only where every blocked row agrees on
        # one, and left empty otherwise. Unanimity is the only signal on
        # offer that is not a guess.
        orgs = {o for o in g["orgs"] if o}
        org = orgs.pop() if len(orgs) == 1 and len(g["orgs"]) == 1 else ""
        if not include_suppressed and is_suppressed(suppressed, key, org):
            continue
        out.append({
            "key": key,
            "row_id": row_id(key),
            "canonical_guess": spellings[0],
            "observed_spellings": spellings,
            "n_rows_blocked": len(g["commitment_ids"]),
            "n_meetings": len(g["refs"]),
            "source_refs": sorted(g["refs"]),
            "org_id": org,
            "commitment_ids": list(g["commitment_ids"]),
        })
    out.sort(key=lambda c: (-c["n_rows_blocked"], -c["n_meetings"], c["key"]))
    return out


# ---------------------------------------------------------------------------
# Render (ONE row shape, three surfaces)
# ---------------------------------------------------------------------------

def context_line(candidate: dict) -> str:
    """The row's one context line: how much is stuck behind this name, across
    how many calls, and which spellings were heard. Counts and spellings —
    never a score, never a guess about who the person is."""
    n = int(candidate.get("n_rows_blocked") or 0)
    m = int(candidate.get("n_meetings") or 0)
    bits = [f"{n} waiting {'capture' if n == 1 else 'captures'} "
            f"blocked on this name",
            f"heard in {m} {'call' if m == 1 else 'calls'}"]
    spellings = list(candidate.get("observed_spellings") or [])
    if len(spellings) > 1:
        bits.append("spelled " + " / ".join(spellings))
    return " · ".join(bits)


CANDIDATE_ROW_ACTIONS = [ACTION_ADD, ACTION_SAME_AS, ACTION_NOT_A_PERSON]


def candidate_rows(candidates, *, cap: int = PROPOSAL_CAP) -> list:
    """The candidates as widget rows — THE one row shape, read by the
    needs-your-call queue, the staff-meeting fold and the End of Day. The row
    carries its own `name_key` so dispatch is stateless: no surface has to
    remember which candidate position 2 was."""
    rows: list = []
    for c in list(candidates or [])[:max(0, int(cap))]:
        rows.append({
            "n": c["row_id"],
            "name": c["canonical_guess"],
            "context_tag": context_line(c),
            "data": {
                "name_key": c["key"],
                "candidate_name": c["canonical_guess"],
                "observed_spellings": list(c.get("observed_spellings") or []),
                "org_id": c.get("org_id") or "",
                "n_rows_blocked": c.get("n_rows_blocked"),
            },
            "actions": list(CANDIDATE_ROW_ACTIONS),
        })
    return rows


def candidate_section(workspace_root, *, now_iso: Optional[str] = None,
                      cap: int = PROPOSAL_CAP,
                      candidates: Optional[list] = None) -> Optional[dict]:
    """One `{title, count, items}` section, or None when there is nothing to
    ask. Drop-empty: an empty frame is never data."""
    cands = (candidates if candidates is not None
             else derive_candidates(workspace_root, now_iso=now_iso))
    rows = candidate_rows(cands, cap=cap)
    if not rows:
        return None
    blocked = sum(int(c.get("n_rows_blocked") or 0) for c in cands)
    title = (f"{SECTION_TITLE} ({len(rows)} of {len(cands)}, "
             f"{blocked} waiting {'capture' if blocked == 1 else 'captures'})")
    return {"title": title, "count": len(rows), "items": rows}


def header_offer(candidates) -> str:
    """The needs-your-call queue's header line — the on-demand offer (§0-3).
    "" when there is nothing to offer, so the header never pads."""
    cands = list(candidates or [])
    if not cands:
        return ""
    blocked = sum(int(c.get("n_rows_blocked") or 0) for c in cands)
    one = len(cands) == 1
    noun = "name has" if one else "names have"
    cnoun = "capture is" if blocked == 1 else "captures are"
    return (f"{len(cands)} recurring {noun} no contact yet — {blocked} "
            f"{cnoun} waiting on them.")


def telemetry(candidates) -> dict:
    """§3-4 — counts only, for the fire receipt. Never a name: the receipt is
    arithmetic, and the same discipline `capture_counts` keeps."""
    cands = list(candidates or [])
    return {
        "n_candidates": len(cands),
        "n_rows_blocked": sum(int(c.get("n_rows_blocked") or 0)
                              for c in cands),
        "n_top_rows_blocked": max(
            [int(c.get("n_rows_blocked") or 0) for c in cands] or [0]),
    }


# ---------------------------------------------------------------------------
# The answer — THE one write path
# ---------------------------------------------------------------------------

def blocked_commitment_ids(workspace_root, key: str) -> set:
    """The OPEN pending rows attributable to ONE candidate's name, right now.

    THE ATTRIBUTION INPUT (review N-2). Derived fresh at answer time rather
    than read off the widget row: a row rendered an hour ago names the ids
    that were stuck THEN, and the honest claim is about the ids that were
    stuck when the user tapped. Fresh derivation also picks up captures that
    landed since the render, which a frozen payload cannot.

    Defensive: a broken log yields an empty set, which makes the ack claim
    NOTHING rather than claim everything."""
    from cru_match import _commitment_id, load_needs_review

    ws = Path(workspace_root)
    out: set = set()
    try:
        rows = load_needs_review(str(_events_path(ws)), workspace_root=str(ws))
    except Exception:  # pragma: no cover — claim nothing on a broken read
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        for raw in _unresolved_names(row):
            if name_key(raw) == key:
                cid = _commitment_id(row)
                if cid:
                    out.add(str(cid))
                break
    return out


def _drain(workspace_root, *, source_skill: str, attributable=(),
           brain_batch_id=None, brain_change_class=None) -> dict:
    """Formalize the rows the new record just released, and report the split.

    `confirm_satisfied_reasons` is the SHIPPED durable-clear path: the
    projection has already released every row whose sole clause the new
    person answers, and this turns that read-side verdict into ordinary
    `clear_review_flags` events. One write path; nothing here re-derives a
    closure.

    THE SPLIT IS THE POINT (review N-2). That writer is WORKSPACE-WIDE and
    takes no name or reason scope: it formalizes every row the projection has
    marked auto-satisfied, including rows answered long ago by unrelated
    conditions. Formalizing them is right — a read-side verdict that is never
    written is re-derived forever, and the standing backlog on a real
    workspace is large — but attributing them to the name the user just
    answered is a lie the ack says out loud. Measured by the reviewer: a
    candidate blocking 2 rows on a workspace with 5 unrelated satisfied rows
    reported 7, and on live substrate the first tap would have claimed ~74
    for one name. That is the count-disagreement class.

    So the wide confirm STAYS, and the caller gets three numbers instead of
    one:

      n_cleared        rows cleared that were provably blocked on THIS name —
                       the writer's own cleared ids INTERSECTED with the ids
                       measured as stuck before the entity write. Nothing is
                       inferred: both halves are observed.
      n_other_cleared  the remainder the wide confirm formalized. Real, and
                       reportable — but never as this name's doing.
      n_total_cleared  what the writer actually wrote, for a receipt.

    ATTENDEE1 — `brain_batch_id` / `brain_change_class` ride through to the
    clears so an AUTOMATIC answer's drain lands in the same undo batch as the
    record that released it. Both None on the user-tap path, which keeps that
    path's events byte-identical: a gesture the user made needs no batch, it
    has the surface's own undo affordance.
    """
    from needs_review_queue import confirm_satisfied_reasons

    res = confirm_satisfied_reasons(workspace_root, source_skill=source_skill,
                                    brain_batch_id=brain_batch_id,
                                    brain_change_class=brain_change_class,
                                    # F-3 — only the rows provably stuck on
                                    # THIS name join the undo batch. The wide
                                    # confirm still formalizes the incidental
                                    # backlog; it just no longer gets reversed
                                    # by an undo that never claimed it.
                                    stamp_only_ids=attributable)
    cleared = {str(r.get("commitment_id"))
               for r in (res.get("results") or [])
               if r.get("status") == "cleared"}
    mine = cleared & {str(c) for c in (attributable or ())}
    return {
        "n_cleared": len(mine),
        "n_other_cleared": len(cleared) - len(mine),
        "n_total_cleared": len(cleared),
        "n_failed": res.get("n_failed") or 0,
    }


def _suppress(workspace_root, *, key: str, name: str, org_id: str,
              answered_by: str, source_skill: str) -> dict:
    """Append the durable ignore. Idempotent: an already-suppressed
    name writes nothing and says so (a second tombstone for one decision is
    the 83-duplicate-row class)."""
    from event_gate import append_event

    existing = load_suppressions(workspace_root)
    skey = f"{key}|{org_key(org_id)}"
    if is_suppressed(existing, key, org_id):
        return {"status": "already_suppressed", "suppression_key": skey}
    append_event(
        _events_path(workspace_root),
        {
            "type": SUPPRESSION_EVENT,
            "source_skill": source_skill,
            "data": {
                "suppression_key": skey,
                "name_key": key,
                "name": name,
                "org_id": org_key(org_id),
                "suppressed_by": answered_by or "",
                # Said in the data so a later reader never has to infer it:
                # this record governs the PROPOSAL and nothing else.
                "scope": "proposal_only",
            },
        },
        holder=f"person_candidates:{source_skill}",
    )
    return {"status": "suppressed", "suppression_key": skey}


def resolve_candidate(workspace_root, *, name_key: str, action: str,
                      canonical_name: Optional[str] = None,
                      observed_spellings: Optional[Iterable[str]] = None,
                      org_id: Optional[str] = None,
                      same_as_person_id: Optional[str] = None,
                      answered_by: str = "",
                      source_skill: str = SOURCE_SKILL,
                      brain_batch_id: Optional[str] = None,
                      brain_change_class: Optional[str] = None) -> dict:
    """Answer ONE candidate. The single write path for this loop.

    THREE ANSWERS, and the action is REQUIRED with no default (the §0-2
    propose-only fence at the write layer — a defaulted action is a person
    record nobody asked for):

      `add person`        create the record through the existing people-crm
                          writer, canonical name from the answer, every
                          observed spelling saved as an alias, then drain.
      `same as [existing]` the name is someone already on file — save the
                          spelling against that record, then drain.
      `not a person`      durable per-name-per-org suppression of the
                          PROPOSAL. Writes nothing else, and touches no
                          capture path: the commitments stay exactly where
                          they are, still pending, still capturable.

    THE ALIASES ARE THE WHOLE POINT. `create_person(aliases=[...])` seeds the
    record's own `aliases` array (entity_resolve Tier 1b) and each spelling
    then also gets an `aliases.json` mapping through `add_person_alias`
    (Tier 1a) — aliases.json keys on the RAW string, so the spelling that
    was failing is exactly the string that must be written. A transcript
    misspelling resolves forever after, which is the difference between
    fixing one row and closing the loop.

    THE DRAIN IS PART OF THE GESTURE. Without it the rows sit released in
    projection and re-derive their release on every read; with it the clear
    is durable history. `n_cleared` is what the ack says out loud, because
    "added them" is a smaller and less true sentence than "added them, and
    nine waiting captures cleared".

    AND IT IS ATTRIBUTED (review N-2). The ids stuck on this name are
    measured BEFORE the entity write, and `n_cleared` reports only the
    intersection of those with what the writer says it cleared. The
    workspace-wide confirm still runs and still formalizes the incidental
    backlog — that is useful work and it is real — but it lands in
    `n_other_cleared`, never in the sentence about the name. Both halves are
    observed; nothing is inferred, and no cause is asserted that the writer
    did not return.

    Returns {"status", "person_id", "n_cleared", "n_other_cleared",
             "n_total_cleared", "aliases_written", "suppression_key",
             "detail"}. Statuses are classified in `apply_audit` (G35):
    created / added / exists / suppressed / already_suppressed /
    already_on_file / needs_confirm / error.

    ATTENDEE1 — `brain_batch_id` / `brain_change_class` are OPTIONAL and both
    None on the USER-TAP path, which is what keeps that path's events
    byte-identical. `auto_answer_candidates` supplies them so ONE `undo`
    reverses the record and the clears together. Nothing about the answer
    changes when they are set: the same gate, the same writer, the same drain —
    only the two stamps that make the writes findable.
    """
    verb = str(action or "").strip().lower()
    if verb not in CANDIDATE_ACTIONS:
        raise ValueError(
            f"resolve_candidate got action={action!r}. A candidate is a "
            f"PROPOSAL: it is answered by one of {list(CANDIDATE_ACTIONS)} "
            f"and by nothing else. There is deliberately no default — a "
            f"defaulted answer here would create a person record off a "
            f"transcript spelling, which is the exact write this loop "
            f"refuses to make on its own.")
    key = str(name_key or "").strip()
    if not key:
        raise ValueError("resolve_candidate needs the row's name_key")
    name = (canonical_name or "").strip() or key
    # The org id is written to the RECORD verbatim (ids are case-sensitive)
    # and folded only for the suppression key.
    org = org_id.strip() if isinstance(org_id, str) else ""
    spellings = [s for s in (observed_spellings or []) if isinstance(s, str)
                 and s.strip()]

    out = {"status": "error", "person_id": None, "n_cleared": 0,
           "n_other_cleared": 0, "n_total_cleared": 0,
           "aliases_written": [], "suppression_key": None, "detail": ""}

    # ATTENDEE1 — THE TWO HALVES OF ONE BATCH CARRY DIFFERENT CLASSES, and
    # conflating them would produce a batch `undo_batch` lists and cannot
    # reverse. `brain_change_class` names the reverser for the RECORD
    # (person_org_creation_structured_fact -> archive); the CLEARS are
    # `commitment_updated` events and their reverser is `commitment_confirm`
    # -> restore_review_flags. One batch id, two classes, both registered.
    _clear_class = None
    if brain_batch_id is not None:
        from attendee_evidence import CLEAR_CHANGE_CLASS as _clear_class

    if verb == ACTION_NOT_A_PERSON:
        res = _suppress(workspace_root, key=key, name=name, org_id=org,
                        answered_by=answered_by, source_skill=source_skill)
        out.update(res)
        return out

    from people_writer import (DuplicatePersonError, MultipleCandidatesError,
                              add_person_alias, create_person)

    # ATTRIBUTION, MEASURED FIRST (review N-2). These are the rows provably
    # stuck on THIS name at the moment of the tap. It has to happen before the
    # entity write, because the write is what releases them — afterwards they
    # are indistinguishable from the incidental backlog the wide confirm also
    # formalizes.
    attributable = blocked_commitment_ids(workspace_root, key)

    if verb == ACTION_SAME_AS:
        pid = str(same_as_person_id or "").strip()
        if not pid:
            raise ValueError(
                "'same as [existing]' needs the person id it points at — "
                "resolve the typed name through the standard entity path "
                "first, and ask when it is ambiguous rather than guessing.")
        out["person_id"] = pid
        written = []
        for spelling in (spellings or [name]):
            try:
                res = add_person_alias(workspace_root, pid, spelling,
                                       source_skill=source_skill)
            except (ValueError, KeyError) as exc:
                out["status"] = "error"
                out["detail"] = str(exc)
                return out
            if res.get("status") == "added":
                written.append(spelling)
        out["aliases_written"] = written
        out["status"] = "added" if written else "exists"
    else:
        try:
            record = create_person(
                workspace_root, canonical_name=name,
                primary_org_id=org or None,
                aliases=[s for s in spellings if _norm(s) != _norm(name)]
                or None,
                needs_enrichment=True, source_skill=source_skill,
                brain_batch_id=brain_batch_id,
                brain_change_class=brain_change_class)
        except MultipleCandidatesError as exc:
            # The name collides with an existing record ambiguously — that is
            # a human decision (Bug #19), never an auto-route.
            out["status"] = "needs_confirm"
            out["detail"] = str(exc)
            return out
        except DuplicatePersonError as exc:
            # Somebody added them between the render and the tap. Nothing to
            # create — but the rows are drainable, so fall through to it.
            out["status"] = "already_on_file"
            out["detail"] = str(exc)
            drained = _drain(workspace_root, source_skill=source_skill,
                             attributable=attributable,
                             brain_batch_id=brain_batch_id,
                             brain_change_class=_clear_class)
            out["n_cleared"] = drained["n_cleared"]
            out["n_other_cleared"] = drained["n_other_cleared"]
            out["n_total_cleared"] = drained["n_total_cleared"]
            return out
        except ValueError as exc:
            out["detail"] = str(exc)
            return out
        out["person_id"] = record.get("id")
        written = []
        for spelling in spellings:
            if _norm(spelling) == _norm(name):
                continue
            try:
                res = add_person_alias(workspace_root, record["id"], spelling,
                                       source_skill=source_skill)
            except (ValueError, KeyError):
                # A spelling already pointing at somebody else is a conflict
                # for a human, not a reason to unwind a good record.
                continue
            if res.get("status") == "added":
                written.append(spelling)
        out["aliases_written"] = written
        out["status"] = "created"

    drained = _drain(workspace_root, source_skill=source_skill,
                     attributable=attributable,
                     brain_batch_id=brain_batch_id,
                     brain_change_class=_clear_class)
    out["n_cleared"] = drained["n_cleared"]
    out["n_other_cleared"] = drained["n_other_cleared"]
    out["n_total_cleared"] = drained["n_total_cleared"]
    return out


# ---------------------------------------------------------------------------
# ATTENDEE1 §3-3 — the auto-answer half: evidence settles a STANDING proposal
# ---------------------------------------------------------------------------

def auto_answer_candidates(workspace_root, *, records_by_ref,
                           candidates: Optional[list] = None,
                           cap: Optional[int] = None,
                           now_iso: Optional[str] = None,
                           answered_by: str = "",
                           source_skill: str = SOURCE_SKILL) -> dict:
    """Answer the candidates the ATTENDEE LIST already settles, and render them
    as RECEIPTS rather than as questions (§3-3).

    THE SAME BAR AS THE INGEST SEAM, applied to the standing stock: a
    candidate's `canonical_guess` must exact-normalize onto an attendee
    carrying an email, reachable from one of that candidate's OWN
    `source_refs`. Not any meeting — the meetings this name was actually heard
    in, which is the same scoping rule `evidence_for_capture` enforces for a
    single capture, for the same reason.

    IT FIRES THE EXISTING ANSWER PATH. `resolve_candidate(action="add person")`
    — the one write path this module already has — so the entity write, the
    durable drain and the attributed counts are all the shipped ones. Nothing
    here re-implements an answer, and there is no second way to create a person
    from a candidate.

    ONE DELIBERATE NARROWING OF THAT CALL: `observed_spellings` is the
    canonical name ALONE, so NO ALIAS IS WRITTEN. See `attendee_evidence`'s
    WHY NO ALIASES header — `people_writer` has no alias-removal path, so an
    alias is a write the undo cannot take back, and under an exact-normalized
    bar every alias it would write differs from canonical only by case or
    whitespace, which `entity_resolve._normalize` already folds. The user-tap
    path is unchanged and still writes every spelling.

    THE RECURRENCE BAR IS NOT TOUCHED. This reads `derive_candidates`, so a
    name below the ruled >= RECURRENCE_MIN_SOURCE_REFS bar is not a candidate
    and is invisible here — by design, and it is why this drains nothing on a
    workspace whose gap is all one-meeting names. Widening it is a ruling, not
    a build decision.

    THE IGNORE STILL WINS. `derive_candidates` already filters suppressed
    names; `attendee_evidence.seed_person_from_evidence`'s fence is not on this
    path because `resolve_candidate` is, so the filter is asserted here again
    rather than assumed — an ignored name that reached this loop would be a
    write against an explicit "no".

    Capped at `AUTO_CREATE_CAP` (3): a backlogged workspace drains over days.

    Returns {"answered", "receipt_lines", "n_auto_created", "n_cleared",
             "n_other_cleared", "n_skipped_no_evidence", "n_skipped_suppressed",
             "batch_id", "counts"}.
    """
    from attendee_evidence import (AUTO_CREATE_CAP, CHANGE_CLASS,
                                   CLEAR_CHANGE_CLASS, find_evidence,
                                   new_batch_id, normalize_attendee_records,
                                   receipt_line)

    limit = AUTO_CREATE_CAP if cap is None else max(0, int(cap))
    cands = (candidates if candidates is not None
             else derive_candidates(workspace_root, now_iso=now_iso))
    by_ref = dict(records_by_ref or {})
    # Review F-1 — the readability verdict travels with the keys here too:
    # an unreadable ledger must not read as "nothing is suppressed" on an
    # automatic rail. Unreadable => this pass answers NOTHING.
    suppressed, ledger_ok = load_suppressions_checked(workspace_root)

    answered: list = []
    lines: list = []
    n_cleared = n_other = 0
    n_no_evidence = n_suppressed = 0
    # Review F-4 — a person already on file was NOT "added", so it gets its own
    # counter and its own sentence. Folding it into `n_auto_created` made the
    # receipt say "Added <name>" for somebody nobody added, with a null
    # person_id behind it.
    n_already_on_file = 0
    batch_id = new_batch_id(now_iso or "")

    if not ledger_ok:
        # The fence, at the top of the pass rather than per candidate: if the
        # ignore ledger cannot be vouched for, no candidate may be answered
        # automatically. The propose path still shows every one of them.
        return {
            "answered": [], "receipt_lines": [], "n_auto_created": 0,
            "n_cleared": 0, "n_other_cleared": 0,
            "n_skipped_no_evidence": 0, "n_skipped_suppressed": len(cands),
            "n_already_on_file": 0, "batch_id": None,
            "clear_change_class": CLEAR_CHANGE_CLASS,
            "ledger_ok": False,
            "counts": {"n_auto_created": 0, "n_auto_rows_cleared": 0},
        }

    for cand in cands:
        if len(answered) >= limit:
            break
        key = cand.get("key") or ""
        guess = cand.get("canonical_guess") or ""
        # THE IGNORE FENCE, asserted not assumed (see the docstring).
        if is_suppressed(suppressed, key, cand.get("org_id") or ""):
            n_suppressed += 1
            continue
        hit = None
        for ref in (cand.get("source_refs") or []):
            ev = find_evidence(guess,
                               normalize_attendee_records(by_ref.get(ref)))
            if ev.get("matched"):
                ev["meeting_ref"] = ref
                hit = ev
                break
        if hit is None:
            n_no_evidence += 1
            continue
        res = resolve_candidate(
            workspace_root,
            name_key=key,
            action=ACTION_ADD,
            # The ATTENDEE RECORD's spelling is the canonical name, not the
            # transcript's guess: the attendee list is the better authority on
            # how the person spells their own name, and that is the whole
            # reason this evidence outranks a proposal.
            canonical_name=hit.get("attendee_name") or guess,
            observed_spellings=[hit.get("attendee_name") or guess],
            org_id=cand.get("org_id") or None,
            answered_by=answered_by,
            source_skill=source_skill,
            brain_batch_id=batch_id,
            brain_change_class=CHANGE_CLASS,
        )
        if res.get("status") not in ("created", "already_on_file"):
            n_no_evidence += 1
            continue
        if res.get("status") == "already_on_file":
            n_already_on_file += 1
        entry = {
            "key": key,
            "person_id": res.get("person_id"),
            "canonical_name": hit.get("attendee_name") or guess,
            "org_domain": hit.get("org_domain") or "",
            "meeting_ref": hit.get("meeting_ref") or "",
            "status": res.get("status"),
            "n_cleared": int(res.get("n_cleared") or 0),
            "batch_id": batch_id,
        }
        answered.append(entry)
        lines.append(receipt_line(entry, n_cleared=entry["n_cleared"]))
        n_cleared += entry["n_cleared"]
        n_other += int(res.get("n_other_cleared") or 0)

    return {
        "answered": answered,
        "receipt_lines": lines,
        # Review F-4 — CREATIONS only. `answered` also holds the
        # already-on-file rows, which were linked rather than created, so
        # counting them here made the number claim work nobody did.
        "n_auto_created": len(answered) - n_already_on_file,
        "n_already_on_file": n_already_on_file,
        "n_cleared": n_cleared,
        "n_other_cleared": n_other,
        "n_skipped_no_evidence": n_no_evidence,
        "n_skipped_suppressed": n_suppressed,
        "batch_id": batch_id if answered else None,
        # The class the CLEARS carried, restated so a reader of this return
        # never has to guess which reverser reopens them.
        "clear_change_class": CLEAR_CHANGE_CLASS,
        "ledger_ok": True,
        "counts": {"n_auto_created": len(answered) - n_already_on_file,
                   "n_auto_rows_cleared": n_cleared},
    }


def ack_line(result: dict, *, name: str) -> str:
    """The one plain-English sentence the surface says back. Counts, never
    event names — and it SAYS the drain, because the drain is the payoff.

    THE ATTRIBUTED COUNT ONLY (review N-2). `n_cleared` is the rows that were
    provably stuck on THIS name. The incidental backlog the workspace-wide
    confirm also formalized gets its own clause, in words that claim no cause
    — "unrelated" is doing real work in that sentence, because the previous
    version of this line said all of them were this person's doing, and on a
    real workspace that would have been ~74 rows credited to one name.

    The clause appears ONLY when the writer actually returned a remainder;
    nothing is padded and nothing is guessed."""
    status = str((result or {}).get("status") or "")
    n = int((result or {}).get("n_cleared") or 0)
    other = int((result or {}).get("n_other_cleared") or 0)
    cleared = (f" — {n} waiting {'capture' if n == 1 else 'captures'} cleared"
               if n else "")
    if other and cleared:
        cleared += (f", plus {other} unrelated stale "
                    f"{'flag' if other == 1 else 'flags'} tidied up")
    elif other:
        cleared = (f" — nothing was waiting on them; {other} unrelated stale "
                   f"{'flag' if other == 1 else 'flags'} tidied up")
    if status == "created":
        return f"Added {name}{cleared}."
    if status in ("added", "exists"):
        return f"{name} now resolves to your existing contact{cleared}."
    if status == "already_on_file":
        return f"{name} was already on file{cleared}."
    if status == "suppressed":
        return f"Got it — I will stop asking about {name}."
    if status == "already_suppressed":
        return f"{name} was already set aside."
    if status == "needs_confirm":
        return (f"{name} might be someone you already have — say which "
                f"record, or that they are new.")
    return f"Could not add {name}: {(result or {}).get('detail') or 'unknown'}"


__all__ = [
    "RECURRENCE_MIN_SOURCE_REFS",
    "PROPOSAL_CAP",
    "SUPPRESSION_EVENT",
    "SECTION_TITLE",
    "ROW_ID_PREFIX",
    "ACTION_ADD",
    "ACTION_SAME_AS",
    "ACTION_NOT_A_PERSON",
    "CANDIDATE_ACTIONS",
    "CANDIDATE_ROW_ACTIONS",
    "name_key",
    "org_key",
    "suppression_key",
    "row_id",
    "load_suppressions",
    "is_suppressed",
    "blocked_commitment_ids",
    "derive_candidates",
    "candidate_rows",
    "candidate_section",
    "context_line",
    "header_offer",
    "telemetry",
    "resolve_candidate",
    "ack_line",
]
