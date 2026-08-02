#!/usr/bin/env python3
"""
commitment_parties.py — THE multi-counterparty reader (v4.6.0 MC1).

ONE commitment, N counterparties. "Send the deck to the board" is a single
commitment owed to three people; before MC1 the schema baked in exactly one
`data.counterparty_id`, so the chase chased one board member and could never
mark the others received. MC1 adds an OPTIONAL `data.counterparty_ids` list
ALONGSIDE the legacy single `data.counterparty_id` (which stays valid
FOREVER). This module is the ONE place both shapes are unioned, so every
consumer reads the same roster with no per-call-site shape logic.

THE MODEL (mirrors the single-field contract exactly)
=====================================================
- `data.counterparty_id`   the legacy single, RESOLVED counterparty (person
                           id). Kept forever; a single-counterparty writer
                           never changes. When a multi-counterparty writer
                           sets the list it ALSO sets this field to the FIRST
                           (primary) counterparty — so any reader this
                           marathon missed still degrades to the first
                           counterparty rather than seeing none (the MC1
                           fail-safe, structural).
- `data.counterparty_ids`  the FULL ordered list of RESOLVED counterparties,
                           including the primary as element 0. Optional; its
                           absence means "single counterparty" (read the
                           scalar).
- `data.counterparty_name` the legacy single UNRESOLVED counterparty (free
                           text, set ONLY when there is no id — the exact
                           `gate_commitment_data` rule).
- `data.counterparty_names` the list of UNRESOLVED counterparties (free text,
                           each WITHOUT an id). DISJOINT from the ids —
                           display names for resolved ids come from entities
                           at render time, never from this field, so the
                           roster never double-counts a person.

THE DISJOINTNESS RIDER (F-28, 2026-07-30)
=========================================
That last sentence is the CONTRACT, and real writers break it. Observed in a
live natural experiment: the same person written as BOTH `counterparty_id`
(resolved) AND `counterparty_name` (the same person's name, free text). The
raw union counted them as TWO counterparties, so `has_multiple_counterparties`
went True, MC1 downgraded an outright close to per-leg partial receipts — and
a name-only leg can NEVER receive one (name-only matches are skipped-and-
reported by design, precisely so no writer guesses an id from a name token).
`all_counterparties_received` therefore never became true and the item was
PERMANENTLY unclosable through that path: purgatory, not a miscount.

So the roster now drops a `counterparty_name` that RESOLVES to an id already
in the set. Two things make that safe:

  * it needs a `workspace_root` — the entity graph lives on disk, and this
    module reads no disk of its own. Every roster reader takes an OPTIONAL
    keyword-only `workspace_root`; omit it and you get the raw union,
    byte-identical to pre-F-28, so no existing caller changes behavior.
  * resolution is EXACT ONLY (`entity_resolve.resolve_all(...,
    min_confidence=1.0)` — alias graph + canonical/alias names). Dropping a
    leg is the LENIENT direction: it lets an item close. Fuzzy and phonetic
    matching guess, and a guess that lets something close is the wrong kind of
    guess. A name that resolves to a DIFFERENT person, or resolves to nothing
    at all, still counts — that is a genuine second counterparty.

Per-person receipt (MC1): `data.received_from` (resolved ids) and
`data.received_from_names` (unresolved names) accumulate on the PROJECTION as
`commitment_partial_received` events land (the loader folds them). A
commitment auto-proposes closure when every counterparty has delivered
(`all_counterparties_received`) — PROPOSE, never auto-close.

Pure dict operations, stdlib only, ZERO domain imports AT IMPORT TIME — safe
to import from anywhere (cru_match, commitment_state, the capture writers, the
surfaces) with no circular-import risk. The F-28 rider's `entity_resolve`
import is LAZY and inside the one function that needs it, so importing this
module still pulls in nothing but the stdlib. Accepts EITHER a commitment
event dict or its `data` payload dict (auto-detected).
"""
from __future__ import annotations

from typing import Any, Optional


def _data(obj: Any) -> dict:
    """The commitment's data payload, given either the event dict (has a
    nested `data` dict) or the data dict itself (a commitment's data payload
    never carries its own `data` key, so the detection is unambiguous)."""
    if not isinstance(obj, dict):
        return {}
    d = obj.get("data")
    if isinstance(d, dict):
        return d
    return obj


def _str_list(value) -> list:
    """A clean, order-preserving, de-duplicated list of non-empty strings from
    a str (singleton) or list value; anything else → []."""
    out: list = []
    if isinstance(value, str):
        value = [value]
    if isinstance(value, list):
        for x in value:
            if isinstance(x, str):
                s = x.strip()
                if s and s not in out:
                    out.append(s)
    return out


def counterparty_ids(obj) -> list:
    """The ordered union of RESOLVED counterparty person-ids: the legacy
    scalar `counterparty_id` (primary, first) then `counterparty_ids`,
    de-duplicated, order preserved. [] when none."""
    d = _data(obj)
    out: list = []
    single = d.get("counterparty_id")
    if isinstance(single, str) and single.strip():
        out.append(single.strip())
    for x in _str_list(d.get("counterparty_ids")):
        if x not in out:
            out.append(x)
    return out


# F-28 — resolution memo, keyed by (workspace, entity-graph state, name). The
# roster readers run once per open commitment per fire, and the ones that
# matter run inside loops over the whole open set; re-reading entities.json for
# every (item, name) pair is the kind of cost that gets a guard reverted. The
# key carries a stat signature of BOTH files the resolver reads, so a fire that
# writes a new person does not answer from a stale cache. Bounded by the number
# of distinct names in one process, which is the roster's own size.
_RESOLVE_CACHE: dict = {}


def _entity_graph_signature(workspace_root) -> tuple:
    """(size, mtime_ns) for entities.json and aliases.json — the cache key's
    freshness half. A missing file contributes a stable sentinel rather than an
    error: 'no file' is itself a state worth caching."""
    from pathlib import Path as _Path
    sig: list = []
    base = _Path(workspace_root) / "_hq" / "data"
    for name in ("entities.json", "aliases.json"):
        try:
            st = (base / name).stat()
            sig.append((st.st_size, st.st_mtime_ns))
        except OSError:
            sig.append(None)
    return tuple(sig)


def _resolved_entity_ids(name: str, workspace_root) -> frozenset:
    """Every entity id `name` resolves to EXACTLY (confidence 1.0 — the alias
    graph and canonical/alias names, never fuzzy or phonetic).

    Exact-only is the whole safety argument (see the module docstring): a hit
    here DROPS a counterparty leg, which is what lets an item close, so it must
    be a fact about the entity graph rather than a similarity score.

    Defensive by design: an unreadable or absent entity graph returns the empty
    set, which means "nothing was matched", which means the name KEEPS its
    place in the roster. The safe direction on this rail is the higher count —
    a spurious extra counterparty produces a chase row someone can dismiss; a
    spuriously dropped one closes a commitment nobody delivered."""
    key = (str(workspace_root), _entity_graph_signature(workspace_root), name)
    hit = _RESOLVE_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        import entity_resolve
        out = frozenset(
            r.entity_id for r in entity_resolve.resolve_all(
                workspace_root, name, min_confidence=1.0)
            if r.entity_id
        )
    except Exception:
        # A fresh workspace (no entities.json), a mid-sync unreadable file, a
        # resolver that raised on a shape it did not expect — none of those are
        # this module's business, and none may take a roster read down.
        out = frozenset()
    _RESOLVE_CACHE[key] = out
    return out


def counterparty_names(obj, *, workspace_root=None) -> list:
    """The ordered union of UNRESOLVED counterparty names (free text, no id):
    legacy scalar `counterparty_name` then `counterparty_names`. [] when
    none. Disjoint from `counterparty_ids` by construction (a resolved
    counterparty carries an id, not a name).

    F-28 — "by construction" is the contract and writers break it. With a
    `workspace_root`, a name that resolves EXACTLY to an id already in
    `counterparty_ids(obj)` is dropped: it is the same person written twice,
    and counting them separately makes the item unclosable (module docstring).
    Without one, the raw union — byte-identical to pre-F-28."""
    d = _data(obj)
    out: list = []
    single = d.get("counterparty_name")
    if isinstance(single, str) and single.strip():
        out.append(single.strip())
    for x in _str_list(d.get("counterparty_names")):
        if x not in out:
            out.append(x)
    if workspace_root is None or not out:
        return out
    # Only a commitment carrying BOTH shapes can exhibit the defect, so the
    # entity read never happens for the id-only or name-only majority.
    ids = set(counterparty_ids(obj))
    if not ids:
        return out
    return [nm for nm in out
            if not (_resolved_entity_ids(nm, workspace_root) & ids)]


def primary_counterparty_id(obj) -> Optional[str]:
    """The FIRST resolved counterparty — the documented degrade for any
    consumer that can only handle a single counterparty (MC1 fail-safe: use
    this, never crash, never silently drop the whole commitment)."""
    ids = counterparty_ids(obj)
    return ids[0] if ids else None


def primary_counterparty_name(obj, *, workspace_root=None) -> Optional[str]:
    """The FIRST unresolved counterparty name — the single-value degrade for
    name-only commitments."""
    names = counterparty_names(obj, workspace_root=workspace_root)
    return names[0] if names else None


def counterparty_count(obj, *, workspace_root=None) -> int:
    """Total counterparties on the commitment (resolved ids + unresolved
    names). With a `workspace_root`, one person written as BOTH an id and that
    person's name counts ONCE (F-28)."""
    return (len(counterparty_ids(obj))
            + len(counterparty_names(obj, workspace_root=workspace_root)))


def has_multiple_counterparties(obj, *, workspace_root=None) -> bool:
    """True iff the commitment names more than one counterparty (the MC1
    fan-out / per-person-receipt path applies).

    THE F-28 SEAM. This predicate is what turns an outright close into per-leg
    partial receipts, so a double-written single counterparty reaching it as
    `True` is what made items unclosable. Pass `workspace_root` on any path
    that can close a commitment."""
    return counterparty_count(obj, workspace_root=workspace_root) > 1


def counterparties(obj, *, workspace_root=None) -> list:
    """The full roster, one entry per counterparty, resolved ids first:
    `[{"id": <pid>, "name": None}, ..., {"id": None, "name": <text>}, ...]`.
    Disjoint — a person appears once (with a `workspace_root`, that holds even
    when a writer wrote the same person as an id AND a name — F-28)."""
    out: list = []
    for cid in counterparty_ids(obj):
        out.append({"id": cid, "name": None})
    for nm in counterparty_names(obj, workspace_root=workspace_root):
        out.append({"id": None, "name": nm})
    return out


def received_from_ids(obj) -> list:
    """Resolved counterparty ids that have delivered (the loader's
    accumulated `data.received_from`)."""
    return _str_list(_data(obj).get("received_from"))


def received_from_names(obj) -> list:
    """Unresolved counterparty names that have delivered (the loader's
    accumulated `data.received_from_names`)."""
    return _str_list(_data(obj).get("received_from_names"))


def outstanding_counterparties(obj, *, workspace_root=None) -> list:
    """The roster MINUS everyone who has delivered — the chase fan-out set
    (one nudge per entry). Same `{"id", "name"}` shape as `counterparties`.
    Names are matched case-insensitively.

    F-28: with a `workspace_root`, a phantom name leg (the same person as an id
    already in the set) never appears here — which is what un-sticks an item
    whose id leg was receipted while the phantom stayed outstanding forever."""
    rid = set(received_from_ids(obj))
    rnm = {n.lower() for n in received_from_names(obj)}
    out: list = []
    for cid in counterparty_ids(obj):
        if cid not in rid:
            out.append({"id": cid, "name": None})
    for nm in counterparty_names(obj, workspace_root=workspace_root):
        if nm.lower() not in rnm:
            out.append({"id": None, "name": nm})
    return out


def all_counterparties_received(obj, *, workspace_root=None) -> bool:
    """True iff the commitment names at least one counterparty AND every one
    of them has delivered — the PROPOSE-closure signal (never an auto-close).
    False for a no-counterparty item (nothing to have received)."""
    if counterparty_count(obj, workspace_root=workspace_root) == 0:
        return False
    return not outstanding_counterparties(obj, workspace_root=workspace_root)


def receipts_are_id_level(events, commitment_id, commitment_seq=None):
    """AUTOAPPLY §4b — `(all_id_level, n_receipts)` over every
    `commitment_partial_received` contributing to one commitment.

    A receipt is ID-LEVEL when it names WHICH counterparty delivered by
    RESOLVED id (`data.received_counterparty_id`) AND carries non-empty
    `data.evidence` — i.e. a connector observed the delivery. A free-text
    name, or an empty-evidence manual claim, is a person's assertion: fine
    as a receipt, never sufficient to close on its own.

    This is what turns a completed roster into CORROBORATION: N independent
    id-level receipts, one per counterparty, each from a connector. Returns
    `(False, n)` the moment ONE receipt falls short — a single evidence-free
    claim in the set sends the whole item back to the confirm row, exactly
    as it renders today."""
    cid = str(commitment_id or "")
    n = 0
    all_ok = True
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        if (ev.get("type") or ev.get("event")) != "commitment_partial_received":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        target = str(d.get("commitment_id") or d.get("target_id")
                     or ev.get("commitment_id") or "")
        seq_match = (commitment_seq is not None
                     and d.get("commitment_seq") == commitment_seq)
        if target != cid and not seq_match:
            continue
        n += 1
        rid = d.get("received_counterparty_id")
        evid = d.get("evidence")
        if not (isinstance(rid, str) and rid.strip()
                and isinstance(evid, str) and evid.strip()):
            all_ok = False
    return (all_ok and n > 0), n


def build_counterparty_fields(
    *,
    counterparty_id=None,
    counterparty_name=None,
    counterparty_ids=None,
    counterparty_names=None,
) -> dict:
    """WRITER-side normalization (v4.6.0 MC1). Given a writer's scalar + list
    counterparty inputs, return the data fields to set on a `commitment`
    event so that:

      - a SINGLE counterparty is BYTE-IDENTICAL to pre-MC1 — the scalar only,
        NO list key (so legacy single-field writers and their golden fixtures
        are unchanged);
      - MULTIPLE counterparties write the full list AND set the scalar to the
        PRIMARY (first) — so every reader, even one this marathon missed,
        degrades to the first counterparty rather than seeing none (the MC1
        fail-safe, structural).

    Resolved ids take precedence; a free-text name is kept only for a
    counterparty with no id (mirrors the single-field `counterparty_name`
    rule). Returns a field dict for the caller to merge into `data`."""
    ids: list = []
    for x in [counterparty_id] + list(counterparty_ids or []):
        if isinstance(x, str) and x.strip() and x.strip() not in ids:
            ids.append(x.strip())
    names: list = []
    for x in [counterparty_name] + list(counterparty_names or []):
        if isinstance(x, str) and x.strip() and x.strip() not in names:
            names.append(x.strip())
    out: dict = {}
    if ids:
        out["counterparty_id"] = ids[0]
    elif names:
        out["counterparty_name"] = names[0]
    if (len(ids) + len(names)) > 1:
        if ids:
            out["counterparty_ids"] = ids
        if names:
            out["counterparty_names"] = names
    return out


__all__ = [
    "counterparty_ids",
    "counterparty_names",
    "primary_counterparty_id",
    "primary_counterparty_name",
    "counterparty_count",
    "has_multiple_counterparties",
    "counterparties",
    "received_from_ids",
    "received_from_names",
    "outstanding_counterparties",
    "all_counterparties_received",
    "receipts_are_id_level",
    "build_counterparty_fields",
]
