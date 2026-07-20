#!/usr/bin/env python3
"""
surface_split.py — THE commitment/task surface partition (SPEC CTS1, 2026-07).

WHY THIS EXISTS
===============
The daily Commitments chat mixed both directions — things people owe the user
and things the user has to do — and the axis that matters for daily action is
"who acts next," not "promise vs to-do" (CTS1 §1; two client calls in July
asked for exactly this split). CTS1 formalizes two named surfaces over the
ONE existing lane:

  - **Waiting On** (Surface 1) — open items someone ELSE acts on next. The
    user nudges; they don't do.
  - **My Plate** (Surface 2) — open items the USER acts on next, in two
    groups: **Promised** (someone's waiting — relationships at stake) and
    **Personal** (only the user's clock is running).

THIS IS A RE-SURFACING MODULE, NOT A LOADER AND NOT A STORE (CTS1 §3, §9).
Both surfaces are read-side filters over `cru_match.load_open_commitments` —
the one projected open set. There is no `tasks.json`, no `direction` field,
no second source of truth. Direction is DERIVED from `owner_id` vs the
primary user; Owed-vs-Task is the EFFECTIVE kind the projector already folds
(`commitment_reclassified` markers applied read-side).

THE CLASSIFIER (CTS1 §2.2 — RULED 2026-07-16, Option B)
=======================================================
Surfaces classify on **effective `kind`**, NEVER on raw counterparty
presence. Live-data verification found 49 of 85 open you-owe items are
`kind: promise` with owner=me and NO resolvable counterparty — the Bug #103
extraction class where counterparty LINKING fails on real promises.
"counterparty empty = Task" would silently demote 49 communicated promises to
personal to-dos (no reconcile-sent close, no aging pressure, wrong surface).
A promise stays a promise when the system merely failed to link who it's for;
those render in My Plate · Promised tagged **counterparty unresolved**
(`counterparty_unresolved()` below) with the §8.2 drip + batch fixup — NEVER
auto-demoted.

THE FIVE-WAY PARTITION (CTS1 §2.4 — the honest invariant)
=========================================================
    waiting_on + promised + personal + unowned + unconfirmed == total

Unowned (no resolvable owner) and unconfirmed (`pending_review`) fall outside
both headline surfaces BY DESIGN; they keep their W4b confirm treatment as
the tail of the Waiting On chat (RULED 2026-07-16 — My Plate stays a pure
act-list). The partition operates over TOP-LEVEL items only (SUB1: the open
set is partitioned via `cru_match.partition_subitems` first — a live
sub-item never appears on either surface; its parent is the row of record).

FILTER TRAP encoded here (CTS1 §2.4): `owner_id != user` is TRUE for a
MISSING owner — the Waiting On test is `owner present AND != user`, or
unowned rows leak into Waiting On.

Edge kinds (CTS1 §2.3): `scheduling`/`agenda` route by the same owner logic
(owner-me, in practice → Personal alongside `task`). Delegated tasks
(owner ≠ me, effective kind `task` — 5 live rows at spec time) land in
Waiting On because someone else acts next, but `cru_match.cru_eligible`
already excludes task-kind from CRU — so they are render-only rows with a
manual nudge, never auto-chased (the surfaces don't change chase policy;
`cru_eligible` remains the ONE chase-eligibility filter).

Counting: this module adds NO new totals. `commitment_state.count_commitments`
stays THE counting API; `partition_surfaces` yields the same headline buckets
re-grouped (waiting_on == headline owed_to_you; promised + personal ==
headline you_owe) and the runtime test pins that parity.

Pure functions over an already-projected open set — no I/O, no writes.
"""
from __future__ import annotations

import sys
from typing import Optional

try:
    from cru_match import _commitment_field, _is_pending_review, partition_subitems
except ImportError:
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from cru_match import _commitment_field, _is_pending_review, partition_subitems


# Surface names — the vocabulary every consumer renders. "waiting_on" /
# "promised" / "personal" are the three headline buckets; "unowned" /
# "unconfirmed" are the confirm tail (Waiting On chat, W4b treatment).
SURFACE_WAITING_ON = "waiting_on"
SURFACE_PROMISED = "promised"
SURFACE_PERSONAL = "personal"
SURFACE_UNOWNED = "unowned"
SURFACE_UNCONFIRMED = "unconfirmed"

SURFACES = (
    SURFACE_WAITING_ON,
    SURFACE_PROMISED,
    SURFACE_PERSONAL,
    SURFACE_UNOWNED,
    SURFACE_UNCONFIRMED,
)

# Kinds that mean "my own work" when owner-is-me and nobody else is on the
# hook (CTS1 §2.3): task, plus scheduling/agenda with no counterparty. A
# scheduling item WITH a counterparty is a real promise-shaped deliverable
# ("lock Monday with Rio") and stays Promised.
_PERSONAL_KINDS = frozenset({"task", "scheduling", "agenda"})


def effective_kind_of(ev: dict) -> str:
    """Effective kind of a PROJECTED commitment event (the loader has already
    folded `commitment_reclassified` overrides into `data.kind`); missing →
    `promise` forever (the read-side default). Mirrors
    cru_match._commitment_kind — kept local to stay import-light."""
    kind = (ev.get("data") or {}).get("kind")
    return kind if isinstance(kind, str) and kind else "promise"


def has_counterparty_signal(ev: dict, user_person_id: Optional[str]) -> bool:
    """True when anything ties a SECOND party to this commitment. The
    counterparty test goes through `commitment_parties` (the MC1
    counterparty_ids/counterparty_names union — CTS1 §2.2: never the two
    scalar fields alone), plus the requester alias chain (the requester IS
    the counterparty on a you-owe promise), `owner_external`, and any
    non-user person_ids. Same signal set the S6 kind migration used."""
    from commitment_parties import counterparty_ids as _cp_ids
    from commitment_parties import counterparty_names as _cp_names

    if _commitment_field(ev, "requester_id"):
        return True
    d = ev.get("data") or {}
    if _cp_ids(d) or _cp_names(d) or d.get("owner_external"):
        return True
    others = {
        p
        for p in (ev.get("person_ids") or []) + (d.get("person_ids") or [])
        if p and p != user_person_id
    }
    return bool(others)


def counterparty_unresolved(ev: dict, user_person_id: Optional[str]) -> bool:
    """The CTS1 §8.2 projection-side tag (NEVER a stored field): a row that
    classifies My Plate · PROMISED yet carries no counterparty signal — a
    communicated promise whose counterparty LINKING failed (Bug #103 class,
    49 live rows at spec time). Renders with the "counterparty unresolved —
    who was this for?" fixup; NEVER auto-demoted to Personal.

    Defined THROUGH classify_surface so the tag can never disagree with the
    partition: a counterparty-less scheduling/agenda row classifies PERSONAL
    and is therefore never tagged (it has no missing counterparty — it's the
    user's own logistics), and pending_review / unowned / waiting-on rows are
    never tagged either (the confirm tail owns those). The Friday-triage
    batch sweeps ALL opens with this predicate, so tag == Promised-and-
    unlinked must hold by construction, not by caller discipline."""
    if classify_surface(ev, user_person_id) != SURFACE_PROMISED:
        return False
    return not has_counterparty_signal(ev, user_person_id)


def classify_surface(ev: dict, user_person_id: Optional[str]) -> str:
    """Which of the five CTS1 buckets ONE projected top-level open commitment
    belongs to. Precedence (CTS1 §2.4): pending_review first (unconfirmed
    items are not owned yet — W4b), then missing owner (unowned), then
    direction by owner, then effective kind inside owner-me.

    `user_person_id=None` (unresolvable primary user) degrades exactly like
    count_commitments: nothing matches the user, so every owned item lands
    waiting_on — the invariant still holds; nothing vanishes."""
    if _is_pending_review(ev):
        return SURFACE_UNCONFIRMED
    owner = _commitment_field(ev, "owner_id")
    if not owner:
        return SURFACE_UNOWNED
    # §2.4 filter trap: `owner != user` is TRUE for a missing owner — the
    # unowned return above runs FIRST, so this comparison only ever sees a
    # present owner.
    if not (user_person_id and owner == user_person_id):
        return SURFACE_WAITING_ON
    # Owner is the user: Promised vs Personal on EFFECTIVE kind (§2.2,
    # Option B — never raw counterparty presence). scheduling/agenda with a
    # counterparty stays Promised; without one it's the user's own logistics.
    kind = effective_kind_of(ev)
    if kind == "task":
        return SURFACE_PERSONAL
    if kind in _PERSONAL_KINDS and not has_counterparty_signal(ev, user_person_id):
        return SURFACE_PERSONAL
    return SURFACE_PROMISED


def partition_surfaces(
    open_commitments: list[dict],
    user_person_id: Optional[str],
) -> dict:
    """The five-way partition over an already-projected open set (CTS1 §2.4).

    SUB1 interaction: the supplied set is partitioned via
    `cru_match.partition_subitems` FIRST and only TOP-LEVEL items are
    classified — a parent with open children is ONE row (on whichever
    surface its owner/kind put it) and its children appear on NEITHER
    surface (the loader's projection stamps make the parent the row of
    record; orphan children partition top-level and classify normally).

    Returns::

        {
          "waiting_on":  [...],   # owner present, != user (they act next)
          "promised":    [...],   # owner user, effective kind != task
          "personal":    [...],   # owner user, effective kind task (+
                                  #   counterparty-less scheduling/agenda)
          "unowned":     [...],   # no resolvable owner — confirm tail
          "unconfirmed": [...],   # pending_review — confirm tail
          "total":       <int>,   # len(top-level open set)
          "sub_items":   <int>,   # excluded children (diagnostic only)
        }

    Invariant (asserted by tests/run_cts1_surface_split_test.py, never by a
    surface re-deriving its own buckets)::

        len(waiting_on) + len(promised) + len(personal)
          + len(unowned) + len(unconfirmed) == total
    """
    top_level, sub_items = partition_subitems(open_commitments or [])
    out: dict = {name: [] for name in SURFACES}
    for ev in top_level:
        out[classify_surface(ev, user_person_id)].append(ev)
    out["total"] = len(top_level)
    out["sub_items"] = len(sub_items)
    return out


def check_partition_invariant(partition: dict) -> bool:
    """True iff the five buckets sum to total — the CTS1 §2.4 honest
    partition. A False here is a classifier defect, never something to
    patch over at a surface."""
    return (
        sum(len(partition.get(name) or ()) for name in SURFACES)
        == partition.get("total")
    )


__all__ = [
    "SURFACE_WAITING_ON",
    "SURFACE_PROMISED",
    "SURFACE_PERSONAL",
    "SURFACE_UNOWNED",
    "SURFACE_UNCONFIRMED",
    "SURFACES",
    "effective_kind_of",
    "has_counterparty_signal",
    "counterparty_unresolved",
    "classify_surface",
    "partition_surfaces",
    "check_partition_invariant",
]
