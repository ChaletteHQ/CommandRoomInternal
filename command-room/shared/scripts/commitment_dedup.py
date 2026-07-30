#!/usr/bin/env python3
"""
Cross-writer semantic dedup at capture time (v4.6.0 C4).

THE HOLE THIS CLOSES
====================
The dedup key on capture is `(source_ref, title)` — SOURCE-SCOPED
(`.source_refs.idx` content hashes). So the same real-world commitment
captured from a meeting transcript (`granola:X`), a follow-up email
(`gmail:Y`), and the nightly sweep (`session:Z`) is three open items,
structurally unpreventable: three source_refs, three hashes, zero collisions.
The 2026-07 dogfood hit this live — the SAME positioning-session commitments
landed from different writers on consecutive days (F-31 → F-46 window).
This is the other half of the volume complaint (W4c gates surfacing;
C4 stops ledger duplication).

WHAT THIS DOES (and deliberately does NOT do)
=============================================
On any new `commitment` append (hooked caller-agnostically inside
`atomic_append_jsonl`'s events branch, same doctrine as the event gate /
source_ref index), each new commitment is compared against OPEN commitments
captured within `DUP_WINDOW_DAYS`. A suspected duplicate is:

  - NOT silently dropped,
  - NOT silently merged,
  - it LANDS, flagged: `data.pending_review: true` +
    `data.suspected_duplicate_of: <canonical id of the open item>` +
    `data.suspected_duplicate_score`, with the reason appended to
    `data.review_reason`.

The confirm flow (W4b) renders the flag as "looks like a duplicate of X —
merge / keep both". Until W4b ships, pending_review rows already render
confirm-not-chase (C1), and the merge itself is the chat phrase documented
in commitment-triage's SKILL.md → `commitment_state.supersede_commitment`.

THE SIMILARITY RULE (conservative — precision over recall)
==========================================================
A false merge-suggestion erodes trust faster than a duplicate, so every
gate must hold:

1. **Window** — the open commitment was captured within DUP_WINDOW_DAYS.
2. **Owner gate** — if BOTH sides carry a resolved `owner_id`, they must be
   equal. (A missing owner on either side does not veto — the real F-31
   sweep captures carried no owner while the meeting writer's did — but it
   contributes no corroboration either; see tier rule below.)
3. **Counterparty gate** — the two sides' counterparty signals must agree:
   resolved ids equal when both present; otherwise counterparty NAME tokens
   must overlap, where a side with no counterparty fields falls back to its
   TITLE tokens (the Bug #103 recall pattern: the sweep wrote "…to
   Skylar…" in the title with empty Stage-E fields). `counterparty_id` is
   expanded to name tokens via entities.json when available. Two sides that
   BOTH have zero counterparty signal (self-owed tasks) pass vacuously.
4. **Title gate** — strong overlap between the titles with BOTH sides'
   person-name tokens stripped first, so the deliverable — not the person —
   carries the match. This is what keeps the near-miss unflagged: "send
   Skyler the positioning brief" vs "send Skyler the invoice" share only
   the person + verb. Score = max(unigram overlap coefficient, bigram
   Jaccard), same machinery as cru_match.
5. **Tier rule** — with at least one POSITIVE person corroboration (owner
   ids equal, counterparty ids equal, or counterparty names matched) the
   title bar is DUP_TITLE_STRONG. With none (everything passed vacuously)
   the bar rises to DUP_TITLE_UNCORROBORATED — title-only evidence must be
   near-verbatim before we ask the user anything.

Items within one append batch are NOT compared to each other: one batch is
one writer over one source, and sibling extractions from a single meeting
are distinct items by construction (one call's four extracted asks are four
commitments — extraction pre-splits; see the grouped-completion doctrine).

Fail-open: flagging is an enhancement — a dedup-check failure must NEVER
fail or lose a capture (worst case a duplicate lands unflagged, which is
exactly today's behavior). `CR_DEDUP_CHECK=0` disables the hook.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    from cru_match import (
        _bigrams,
        _commitment_field,
        _commitment_id,
        _is_pending_review,
        _jaccard,
        _overlap_coefficient,
        _tokenize,
        load_open_commitments,
    )
    from event_time import event_time
except ImportError:  # direct-path import (tests, bash one-liners)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cru_match import (
        _bigrams,
        _commitment_field,
        _commitment_id,
        _is_pending_review,
        _jaccard,
        _overlap_coefficient,
        _tokenize,
        load_open_commitments,
    )
    from event_time import event_time

# Only open commitments captured this recently are duplicate candidates. The
# real cross-writer pairs land hours-to-days apart (meeting -> follow-up email
# -> nightly sweep); a same-title item from months ago is far more likely a
# recurring real ask than a duplicate capture.
DUP_WINDOW_DAYS = 14

# Title bar with at least one positive person corroboration (owner ids equal /
# counterparty ids equal / counterparty names matched). Tuned on the F-31
# fixture pair: the sweep's verbose title vs the meeting writer's tight one
# score ~0.83-0.86 after name-stripping; the same-person-different-deliverable
# near-miss scores <=0.5.
DUP_TITLE_STRONG = 0.7

# Title bar when EVERY person gate passed vacuously (no owner or counterparty
# signal on either side — e.g. two bare self-owed tasks). Title-only evidence
# must be near-verbatim before we spend an ask on it.
DUP_TITLE_UNCORROBORATED = 0.85

# AUTOAPPLY §4c — the AUTO-MERGE bar. Above DUP_TITLE_STRONG by design: this
# tier does not ask, so "near-verbatim" is the floor, not "strong enough to
# be worth a question".
DUP_TITLE_AUTO = 0.9


def _person_name_index(workspace_root) -> dict:
    """person_id -> set of name tokens (canonical_name + aliases), read
    best-effort from entities.json. Used to expand a resolved counterparty_id
    into comparable name tokens when the other writer only wrote a free-text
    name (or only named the person in the title). Empty dict on any failure —
    the check then just leans on counterparty_name/title tokens."""
    out: dict = {}
    try:
        p = Path(workspace_root) / "_hq" / "data" / "entities.json"
        if not p.exists():
            return out
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            return out
        inner = data.get("entities") if isinstance(data.get("entities"), dict) else data
        for person in inner.get("people") or []:
            if not isinstance(person, dict):
                continue
            pid = person.get("id")
            if not pid:
                continue
            toks: set = set()
            names = [person.get("canonical_name") or person.get("name") or ""]
            aliases = person.get("aliases")
            if isinstance(aliases, list):
                names.extend(a for a in aliases if isinstance(a, str))
            for n in names:
                toks |= {t for t in _tokenize(n) if len(t) >= 3}
            if toks:
                out[str(pid)] = toks
    except Exception:
        return {}
    return out


def _counterparty_signal(ev_data: dict, name_index: dict) -> tuple[frozenset, set]:
    """(counterparty-id SET, name-token set) for one commitment's data. MC1:
    the id set is the FULL roster (legacy single + counterparty_ids list), so
    two captures of the same multi-counterparty commitment agree when their
    rosters OVERLAP. The token set unions every free-text counterparty name
    with each id's entities-resolved names, so an id-only writer and a
    name-only writer can still agree on WHO."""
    from commitment_parties import (
        counterparty_ids as _cp_ids,
        counterparty_names as _cp_names,
    )
    d = ev_data if isinstance(ev_data, dict) else {}
    ids = {str(c) for c in _cp_ids(d)}
    toks: set = set()
    for name in _cp_names(d):
        toks |= {t for t in _tokenize(name) if len(t) >= 3}
    for cid in ids:
        if cid in name_index:
            toks |= name_index[cid]
    return frozenset(ids), toks


def _title_of(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    return str(data.get("title") or data.get("summary") or "")


def _edit_distance_le_1(a: str, b: str) -> bool:
    """True iff Levenshtein(a, b) <= 1 — one insert/delete/substitute."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = j = 0
    edited = False
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        if edited:
            return False
        edited = True
        if la == lb:
            i += 1  # substitution
        j += 1      # insertion into the shorter one
    return True


def _name_tokens_match(a: set, b: set) -> bool:
    """Lenient overlap for PERSON-NAME tokens only: equal, one is a >=4-char
    prefix of the other (Ron/Ronald), or within edit distance 1 for >=5-char
    tokens (Skylar/Skyler — F-53's exact raw-transcript drift). Exact
    equality would miss the real cross-writer pairs, since one writer stores
    the resolved entity name and another the raw spelling. Lenient matching is
    safe here because a name match only OPENS candidacy — the title gate still
    decides."""
    for ta in a:
        for tb in b:
            if ta == tb:
                return True
            short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
            if len(short) >= 4 and long_.startswith(short):
                return True
            if len(short) >= 5 and _edit_distance_le_1(ta, tb):
                return True
    return False


def _expand_name_drop(name_toks: set, title_tokens: Iterable[str]) -> set:
    """Title tokens to strip as person-name noise: exact members of
    `name_toks` plus any title token that lenient-matches one (so "skylar"
    in a title is stripped when the counterparty is "Skyler")."""
    out = set(name_toks)
    for t in title_tokens:
        if _name_tokens_match({t}, name_toks):
            out.add(t)
    return out


def title_similarity(a: str, b: str, *, drop_tokens: Iterable[str] = ()) -> float:
    """max(unigram overlap coefficient, bigram Jaccard) over stopword-filtered
    tokens, with `drop_tokens` (person names — the person gate's job, not the
    title's) removed from both sides first. 0.0 when either side has no
    content tokens left."""
    drop = set(drop_tokens)
    ta = [t for t in _tokenize(a) if t not in drop]
    tb = [t for t in _tokenize(b) if t not in drop]
    if not ta or not tb:
        return 0.0
    uni = _overlap_coefficient(set(ta), set(tb))
    bi = _jaccard(_bigrams(ta), _bigrams(tb))
    return max(uni, bi)


def _parse_dt(value) -> Optional[_dt.datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def score_suspected_duplicate(
    new_data: dict,
    open_ev: dict,
    *,
    name_index: Optional[dict] = None,
    now_dt: Optional[_dt.datetime] = None,
    window_days: int = DUP_WINDOW_DAYS,
) -> Optional[dict]:
    """Score one new capture's `data` against one OPEN commitment event.
    Returns {"commitment_id", "score", "corroborated", "title"} when every
    gate holds, else None. Pure — callers supply the open set and the
    optional person-name index."""
    name_index = name_index or {}
    d_open = open_ev.get("data") if isinstance(open_ev.get("data"), dict) else {}

    # 0. Split-provenance guard (v4.6.0 S4): a split child is BY CONSTRUCTION
    # distinct from the commitment it was carved out of — never flag it
    # against its own parent (the parent is still open at child-append time;
    # a partial title overlap with it is expected, not a duplicate signal).
    # Children vs OTHER opens still compare normally.
    if new_data.get("split_from") and str(new_data["split_from"]) == _commitment_id(open_ev):
        return None
    sseq = new_data.get("source_event_seq")
    if sseq is not None and open_ev.get("seq") == sseq:
        return None

    # 0b. Sub-item guards (SUB1 D6), mirroring the split guard:
    #   (a) never flag a child against its own PARENT — the parent is still
    #       open at child-append time and title overlap with the deliverable
    #       it decomposes is EXPECTED, not a duplicate signal;
    #   (b) never flag two children of the SAME parent against each other —
    #       later-added siblings would otherwise collide with open ones
    #       (in-batch siblings are already exempt by construction).
    #   Children vs UNRELATED opens still compare normally.
    new_pid = new_data.get("parent_id")
    if new_pid and str(new_pid) == _commitment_id(open_ev):
        return None
    pseq = new_data.get("parent_seq")
    if pseq is not None and open_ev.get("seq") == pseq:
        return None
    open_pid = d_open.get("parent_id")
    if new_pid and open_pid and str(new_pid) == str(open_pid):
        return None

    # 1. Window — the open item must be a recent capture.
    if now_dt is not None:
        captured = _parse_dt(event_time(open_ev))
        if captured is None or (now_dt - captured) > _dt.timedelta(days=window_days):
            return None
        if captured > now_dt + _dt.timedelta(days=1):
            return None

    # 2. Owner gate — resolved-and-different is a hard veto.
    new_owner = new_data.get("owner_id") or None
    open_owner = _commitment_field(open_ev, "owner_id") or None
    if new_owner and open_owner and str(new_owner) != str(open_owner):
        return None
    owner_corroborated = bool(new_owner and open_owner and str(new_owner) == str(open_owner))

    # 3. Counterparty gate.
    new_cp_ids, new_cp_toks = _counterparty_signal(new_data, name_index)
    open_cp_ids, open_cp_toks = _counterparty_signal(d_open, name_index)
    cp_corroborated = False
    if new_cp_ids and open_cp_ids:
        # MC1: both sides carry resolved counterparties — they must SHARE at
        # least one. Two captures of "send the deck to the board" overlap on
        # the board members; two fully-disjoint rosters are different
        # commitments (hard veto). Reduces to id-equality for singletons, so
        # single-counterparty behavior is byte-identical.
        if not (new_cp_ids & open_cp_ids):
            return None
        cp_corroborated = True
    else:
        # Name-token comparison (lenient — raw-spelling drift per F-53); a
        # side with no counterparty fields falls back to its title tokens
        # (the F-31 sweep shape: person named in the title, Stage-E fields
        # empty).
        a = new_cp_toks or {t for t in _tokenize(_title_of(new_data)) if len(t) >= 3}
        b = open_cp_toks or {t for t in _tokenize(_title_of(d_open)) if len(t) >= 3}
        if new_cp_toks or open_cp_toks:
            if not _name_tokens_match(a, b):
                return None
            # Corroborated only when a REAL counterparty field matched the
            # other side — title-vs-title token overlap proves nothing about
            # the counterparty.
            cp_corroborated = bool(
                _name_tokens_match(new_cp_toks, b) if new_cp_toks
                else _name_tokens_match(open_cp_toks, a)
            )
        # Neither side has any counterparty signal: vacuous pass (tier rule
        # raises the title bar below).

    # 4 + 5. Title gate at the tier the corroboration earns. Person-name
    # tokens (and their drift spellings) are stripped from BOTH titles first —
    # the person gate above owns identity; the title gate owns the deliverable.
    corroborated = owner_corroborated or cp_corroborated
    threshold = DUP_TITLE_STRONG if corroborated else DUP_TITLE_UNCORROBORATED
    name_toks = new_cp_toks | open_cp_toks
    title_toks = _tokenize(_title_of(new_data)) + _tokenize(_title_of(d_open))
    drop = _expand_name_drop(name_toks, title_toks) if name_toks else set()
    score = title_similarity(_title_of(new_data), _title_of(d_open), drop_tokens=drop)
    if score < threshold:
        return None

    return {
        "commitment_id": _commitment_id(open_ev),
        "score": round(score, 3),
        "corroborated": corroborated,
        "title": (_title_of(d_open) or "")[:120],
    }


def find_suspected_duplicate(
    new_data: dict,
    open_commitments: list[dict],
    *,
    name_index: Optional[dict] = None,
    now_dt: Optional[_dt.datetime] = None,
    window_days: int = DUP_WINDOW_DAYS,
) -> Optional[dict]:
    """Best (highest-scoring) suspected duplicate for one new capture, or
    None. Pure over supplied data."""
    best: Optional[dict] = None
    for open_ev in open_commitments or []:
        m = score_suspected_duplicate(
            new_data, open_ev,
            name_index=name_index, now_dt=now_dt, window_days=window_days,
        )
        if m and (best is None or m["score"] > best["score"]):
            best = m
    return best


def auto_merge_eligible(
    new_data: dict,
    open_ev: dict,
    *,
    name_index: Optional[dict] = None,
    now_dt: Optional[_dt.datetime] = None,
    window_days: int = DUP_WINDOW_DAYS,
) -> Optional[dict]:
    """AUTOAPPLY §4c — the auto-MERGE gate: `{survivor_id, score, predicate}`
    when this new capture and `open_ev` are the same real-world commitment
    beyond adjudication, else None.

    WHY A TIER ABOVE C4 RATHER THAN A RETUNE OF IT. C4 is a ledger-integrity
    fix: it stops silent duplication by FLAGGING, which by construction ADDS
    a confirm row. It converted an invisible cost (duplicate items) into a
    visible one (adjudication rows). This gate sits above it so a duplicate
    the system is certain about costs ZERO rows instead of one.

    Every bar must hold, and each is strictly tighter than C4's:
      * window — same DUP_WINDOW_DAYS as the flag tier (14, unchanged);
      * owner — resolved on BOTH sides and equal. C4 lets a missing owner
        pass vacuously; the auto tier never does. A vacuous pass is the
        absence of evidence, and this tier acts on evidence;
      * counterparty — resolved ids overlapping, or both sides genuinely
        counterparty-free (self-owed). NAME-token agreement is enough to
        ASK but never enough to act: raw-spelling drift is exactly the
        lenient match C4 needs and precision forbids here;
      * title ≥ DUP_TITLE_AUTO (0.9) after person-name stripping;
      * DIFFERENT source_refs — cross-writer capture IS the corroboration
        (two independent systems observed the same real-world ask). Same
        ref twice is one source seen twice, which corroborates nothing —
        §2's independence rule, the same rule the §6 fence enforces;
      * neither side pending_review for any other reason, and the open side
        has no open sub-items (SUB1 D3 — a merge would move them silently).

    Pure over supplied data — no reads, no writes."""
    if new_data.get("pending_review") or _is_pending_review(open_ev):
        return None
    n_open_subs = (open_ev.get("data") or {}).get("n_subitems_open")
    if isinstance(n_open_subs, int) and not isinstance(n_open_subs, bool) \
            and n_open_subs > 0:
        return None
    if new_data.get("parent_id") or (open_ev.get("data") or {}).get("parent_id"):
        return None

    # Reuse the flag tier's structural guards (split/sub-item provenance,
    # window, owner veto, counterparty veto) — one implementation, so the two
    # tiers can never disagree about what is even a candidate.
    base = score_suspected_duplicate(
        new_data, open_ev, name_index=name_index, now_dt=now_dt,
        window_days=window_days)
    if base is None:
        return None

    new_owner = new_data.get("owner_id") or None
    open_owner = _commitment_field(open_ev, "owner_id") or None
    if not (new_owner and open_owner and str(new_owner) == str(open_owner)):
        return None

    d_open = open_ev.get("data") if isinstance(open_ev.get("data"), dict) else {}
    new_cp_ids, _ = _counterparty_signal(new_data, name_index or {})
    open_cp_ids, _ = _counterparty_signal(d_open, name_index or {})
    if new_cp_ids or open_cp_ids:
        # Both sides must carry resolved ids AND overlap. One side resolved
        # and the other name-only is the ASK case, never the act case.
        if not (new_cp_ids and open_cp_ids and (new_cp_ids & open_cp_ids)):
            return None

    new_ref = str(new_data.get("source_ref") or "").strip()
    open_ref = str(d_open.get("source_ref") or
                   open_ev.get("source_ref") or "").strip()
    if not new_ref or not open_ref or new_ref == open_ref:
        return None

    # Re-score at the auto bar. `base["score"]` was computed against the
    # tier's threshold, not this one.
    name_toks = _counterparty_signal(new_data, name_index or {})[1] | \
        _counterparty_signal(d_open, name_index or {})[1]
    title_toks = _tokenize(_title_of(new_data)) + _tokenize(_title_of(d_open))
    drop = _expand_name_drop(name_toks, title_toks) if name_toks else set()
    score = title_similarity(_title_of(new_data), _title_of(d_open),
                             drop_tokens=drop)
    if score < DUP_TITLE_AUTO:
        return None

    return {
        "survivor_id": _commitment_id(open_ev),
        "score": round(score, 3),
        # §7 — one string a later audit reads the REASON off, so "why did you
        # do that?" is answerable from the substrate, not a vanished chat.
        "predicate": f"cross_writer_dup:{round(score, 3)}",
    }


def find_auto_merge(
    new_data: dict,
    open_commitments: list,
    *,
    name_index: Optional[dict] = None,
    now_dt: Optional[_dt.datetime] = None,
    window_days: int = DUP_WINDOW_DAYS,
) -> Optional[dict]:
    """Best auto-mergeable survivor for one new capture, or None. When two
    open items both clear the gate the capture is ambiguous about WHICH it
    duplicates — that is adjudication, so it returns None and the flag tier
    takes over."""
    hits = []
    for open_ev in open_commitments or []:
        m = auto_merge_eligible(
            new_data, open_ev, name_index=name_index, now_dt=now_dt,
            window_days=window_days)
        if m:
            hits.append(m)
    return hits[0] if len(hits) == 1 else None


def stamp_is_stale(ev: dict, *, now_dt: Optional[_dt.datetime] = None,
                   window_days: int = DUP_WINDOW_DAYS) -> bool:
    """Is this stamped capture's auto-merge DECISION older than the window?

    AUTOAPPLY review F-6. The gate's `DUP_WINDOW_DAYS` bar was evaluated once,
    at capture; the D1 split means the action happens a fire later. Nothing
    re-checked it, so a 190-day-old stamp still applied at face value. The
    stamp rides the capture event, so the decision's age IS the stamped
    item's capture age. Unparseable capture ts fails SAFE (stale) — the same
    direction the §6 fence takes on an unreadable timestamp."""
    if now_dt is None:
        now_dt = _dt.datetime.now(_dt.timezone.utc)
    captured = _parse_dt(event_time(ev))
    if captured is None:
        return True
    return (now_dt - captured) > _dt.timedelta(days=window_days)


def has_stale_auto_merge_stamp(
    open_commitments: list,
    *,
    now_dt: Optional[_dt.datetime] = None,
    window_days: int = DUP_WINDOW_DAYS,
) -> bool:
    """Is the apply half demonstrably NOT running? (AUTOAPPLY review F-5.)

    True when any OPEN, not-yet-flagged commitment carries an `auto_merge_of`
    stamp older than the window. A stamp is a decision waiting on
    `apply_auto_merges`; one that aged past the window is proof the daily
    fire never came. C4's guarantee is that a duplicate ALWAYS becomes a
    visible question, and the stamp bypasses the flag tier to buy zero
    adjudication rows — a trade that only pays if the fire actually runs.
    When it doesn't, capture falls back to the flag tier so the guarantee
    holds without the fire.

    Self-healing, and that matters because the stamp can never be erased
    (append-only): once the fire runs again, `apply_auto_merges` flags every
    stale stamp for review, and a flagged item is excluded here — so the
    staleness set drains and the auto tier re-enables on its own. Pending
    review is the exclusion for exactly that reason."""
    for ev in open_commitments or []:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if not d.get("auto_merge_of"):
            continue
        if _is_pending_review(ev):
            continue
        if stamp_is_stale(ev, now_dt=now_dt, window_days=window_days):
            return True
    return False


def _dedup_enabled() -> bool:
    return os.environ.get("CR_DEDUP_CHECK", "1") != "0"


def flag_suspected_duplicates(events: list, events_jsonl_path) -> list:
    """The write-path hook (called from atomic_append_jsonl's events branch,
    after the event gate). For each `commitment` event in the batch, look for
    a suspected duplicate among the OPEN commitments already on disk; on a
    match return a COPY carrying the flag. Non-commitment events and batches
    with no commitments pass through untouched at near-zero cost.

    Never raises and never blocks the append — any failure returns the
    events unmodified with a stderr note (fail-open: an unflagged duplicate
    is today's behavior; a lost capture is a new bug)."""
    if not _dedup_enabled():
        return events
    try:
        if not any(
            isinstance(ev, dict) and ev.get("type") == "commitment"
            for ev in events
        ):
            return events
        path = Path(events_jsonl_path)
        workspace_root = path.parent.parent.parent
        open_commitments = load_open_commitments(path)
        if not open_commitments:
            return events
        name_index = _person_name_index(workspace_root)
        now_dt = _dt.datetime.now(_dt.timezone.utc)
        # AUTOAPPLY review F-5 — the dead-fire fallback, computed ONCE per
        # batch off the open set already in hand. A stamp older than the
        # window that is still sitting unapplied is proof `apply_auto_merges`
        # is not firing; while that is true, capture stops buying zero
        # adjudication rows on credit and goes back to ASKING. This is the
        # site that runs when the daily fire is dead — an apply-time fallback
        # alone would never execute in exactly the case it is meant to cover.
        rail_is_dead = has_stale_auto_merge_stamp(
            open_commitments, now_dt=now_dt)

        out = []
        for ev in events:
            if not (isinstance(ev, dict) and ev.get("type") == "commitment"):
                out.append(ev)
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            # AUTOAPPLY §4c — the auto tier is evaluated FIRST, above the
            # flag. A duplicate the gate is certain about is STAMPED for the
            # merge instead of flagged for a question.
            auto = None if rail_is_dead else find_auto_merge(
                data, open_commitments, name_index=name_index, now_dt=now_dt,
            )
            if auto is not None and auto["survivor_id"] != data.get("id"):
                new_data = {**data,
                            "auto_merge_of": auto["survivor_id"],
                            "auto_merge_score": auto["score"],
                            "auto_predicate": auto["predicate"]}
                out.append({**ev, "data": new_data})
                continue
            match = find_suspected_duplicate(
                data, open_commitments, name_index=name_index, now_dt=now_dt,
            )
            if match is None or match["commitment_id"] == data.get("id"):
                out.append(ev)
                continue
            new_data = {**data}
            new_data["pending_review"] = True
            new_data["suspected_duplicate_of"] = match["commitment_id"]
            new_data["suspected_duplicate_score"] = match["score"]
            reason = f"looks like a duplicate of an open item: {match['title']}"
            existing_reason = new_data.get("review_reason")
            if isinstance(existing_reason, str) and existing_reason.strip():
                new_data["review_reason"] = existing_reason + "; " + reason
            else:
                new_data["review_reason"] = reason
            out.append({**ev, "data": new_data})
        return out
    except Exception as e:  # fail-open, loudly
        sys.stderr.write(
            f"[commitment_dedup] similarity check failed ({type(e).__name__}: "
            f"{e}) — batch appended unflagged\n"
        )
        return events


def apply_auto_merges(workspace_root, *, source_skill: str,
                      batch_id: Optional[str] = None) -> dict:
    """AUTOAPPLY §4c — apply every stamped auto-merge. THE detector half.

    WHY THIS IS SPLIT FROM THE CAPTURE HOOK (a deviation from SPEC §4c,
    which reads "Action: supersede_commitment(newer → older survivor)" in
    the capture hook itself): `flag_suspected_duplicates` runs INSIDE
    `atomic_append_jsonl`, BEFORE the batch is written. The newer commitment
    does not exist on disk yet, and `supersede_commitment` resolves BOTH ids
    through `_scan_commitment_index` — so the specced call cannot succeed
    there. It would also put a policy action (propose/apply/resolve on the
    brain rail) inside a substrate primitive, which is the wrong layer.

    So the GATE is evaluated at capture exactly as specced — pure, no
    writes — and stamps `data.auto_merge_of` on the new event; this applies
    it on the next fire. The window between them costs no adjudication row:
    a stamped capture is NOT flagged pending_review, so it renders as an
    ordinary open item rather than a question. That is already strictly
    better than the flag tier's one-row-per-duplicate.

    LB2 auto lifecycle (FB-20): propose(tier="auto") + apply + resolve in
    the SAME iteration — the auto proposal never rests open. Direction is
    newer-superseded / older-survives (§11: the older item carries the
    accumulated context; refs union either way).

    THE STAMP IS A DECISION, NOT A LICENCE (review F-1/F-3/F-6). The split
    opened a gap between deciding and acting, so this half re-validates the
    WHOLE gate at apply time, not just the one precondition it started with:

      * the pair was not REVERSED by a human (`undone_auto_merges` — the
        durable negation; skipped, never re-applied);
      * the stamped item is not flagged for review (pre-existing);
      * the SURVIVOR is still open and not itself flagged — "both items open"
        is a §4c precondition of equal standing, and `supersede_commitment`
        deliberately allows a CLOSED survivor, which is right for a merge a
        human confirmed while looking at it and wrong for an unattended stamp
        applied a fire later (the live ask would be superseded into an
        abandoned item);
      * the stamp is still inside `DUP_WINDOW_DAYS`.

    Every one of those failures FALLS BACK TO THE FLAG TIER
    (`flag_duplicate_for_review`) instead of skipping quietly, so the pair
    becomes the visible question C4 guarantees. A silent skip leaves the
    duplicate both invisible AND unmerged — strictly worse than never having
    had an auto tier. The reversed case is the one exception: the undo
    already put that question in front of the user, and re-asking after a
    Keep-both would be nagging.

    Returns {"merged": [...], "skipped": [...], "errors": [...]}. Loud
    per-item, never aborts the run."""
    from brain_proposals import propose, resolve_proposal
    # The negation lives with the module that OWNS the events it reads —
    # brain_undo writes the brain_change_undone markers and registers the
    # reverser; "which auto-changes did the user reverse?" is an undo-registry
    # question, not a dedup one.
    from brain_undo import undone_auto_merges
    from commitment_state import flag_duplicate_for_review, supersede_commitment

    ws = Path(workspace_root)
    events_path = ws / "_hq" / "data" / "events.jsonl"
    out: dict = {"merged": [], "skipped": [], "errors": []}
    if not events_path.exists():
        return out
    # §7 — ONE batch per RUN, timestamped, matching the efb_/idr_/pbs_
    # precedents. A per-SKILL constant made every auto-merge that skill ever
    # applied one undoable batch, so a single `undo` reached back across days
    # and reversed runs the user never saw (review F-2).
    batch_id = batch_id or ("amg_" + _dt.datetime.now(_dt.timezone.utc)
                            .strftime("%Y%m%dT%H%M%SZ"))
    # The narrating surface needs the ref it is advertising "say undo" for.
    out["batch_id"] = batch_id

    open_by_id = {}
    for ev in load_open_commitments(events_path):
        open_by_id[_commitment_id(ev)] = ev
    undone = undone_auto_merges(ws)
    now_dt = _dt.datetime.now(_dt.timezone.utc)

    def _fallback_to_flag(cid, survivor_id, why, score=None) -> None:
        """Skip AND ask — never a silent skip, never a silent drop."""
        row = {"commitment_id": cid, "survivor_id": survivor_id, "why": why}
        try:
            res = flag_duplicate_for_review(
                ws, cid, suspected_duplicate_of=survivor_id,
                reason=f"looks like a duplicate — {why}",
                flagged_by=source_skill, source_skill=source_skill,
                score=score)
            row["flagged"] = res.get("status")
        except Exception as exc:  # loud per-item, contained per-run
            row["flagged"] = f"error: {type(exc).__name__}: {exc}"
        out["skipped"].append(row)

    for cid, ev in list(open_by_id.items()):
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        survivor_id = d.get("auto_merge_of")
        if not survivor_id:
            continue
        score = d.get("auto_merge_score")
        # F-1 — a human already reversed THIS merge. The stamp cannot be
        # erased (append-only), so reading the negation here is what makes
        # the undo stick.
        if (str(cid), str(survivor_id)) in undone:
            out["skipped"].append({
                "commitment_id": cid, "survivor_id": survivor_id,
                "why": "you reversed this merge — never re-applied "
                       "automatically"})
            continue
        # Re-check at apply time — the stamp is a DECISION, not a licence.
        # Anything that flagged the item for review since capture wins. This
        # is also what makes the fallback below idempotent: once a stamp has
        # fallen back to the flag tier, the next run stops HERE rather than
        # writing a second flag every day.
        if _is_pending_review(ev):
            out["skipped"].append({"commitment_id": cid,
                                   "why": "flagged for review since capture"})
            continue
        # F-3 — the survivor must still be a legal merge target.
        survivor_ev = open_by_id.get(str(survivor_id))
        if survivor_ev is None:
            _fallback_to_flag(cid, survivor_id,
                              "the item it duplicates was closed after this "
                              "was captured", score)
            continue
        if _is_pending_review(survivor_ev):
            _fallback_to_flag(cid, survivor_id,
                              "the item it duplicates is itself flagged for "
                              "review", score)
            continue
        # F-6 — the window bar was evaluated at capture and nowhere else.
        if stamp_is_stale(ev, now_dt=now_dt):
            _fallback_to_flag(cid, survivor_id,
                              f"the match is older than {DUP_WINDOW_DAYS} "
                              "days and was never applied", score)
            continue
        predicate = d.get("auto_predicate") or "cross_writer_dup"
        try:
            res = propose(
                ws,
                kind="commitment_merge",
                fingerprint=f"commitment_merge:{cid}:{survivor_id}",
                evidence=f"{predicate} — same owner, same counterparty, "
                         f"near-verbatim title, different writers",
                action_tuples=[{"action": "confirm"}, {"action": "hold"}],
                tier="auto",
                change_class="commitment_merge",
                detector=source_skill,
                render_line=(f"Merged a duplicate capture of "
                             f"{(d.get('title') or '')[:80]!r}"),
                extra={"commitment_id": cid, "survivor_id": survivor_id,
                       "auto_predicate": predicate},
            )
            if res.get("status") != "proposed":
                out["skipped"].append({"commitment_id": cid,
                                       "why": res.get("status")})
                continue
            merged = supersede_commitment(
                ws, survivor_id, cid,
                merged_by=source_skill, source_skill=source_skill,
                evidence=predicate,
                auto_merge=True,
                auto_merge_evidence={
                    "auto_predicate": predicate,
                    "auto_merge_score": d.get("auto_merge_score"),
                    "brain_batch_id": batch_id,
                    "brain_change_class": "commitment_merge",
                    "merged_from_writer": d.get("source_ref") or "",
                },
            )
            resolve_proposal(ws, res["proposal_id"], "applied",
                             resolved_by=source_skill,
                             source_skill=source_skill)
            out["merged"].append({"commitment_id": cid,
                                  "survivor_id": merged.get("survivor_id"),
                                  "status": merged.get("status"),
                                  "predicate": predicate})
        except Exception as exc:  # loud per-item, contained per-run
            out["errors"].append({"commitment_id": cid,
                                  "error": f"{type(exc).__name__}: {exc}"})
    return out


__all__ = [
    "DUP_WINDOW_DAYS",
    "DUP_TITLE_STRONG",
    "DUP_TITLE_UNCORROBORATED",
    "DUP_TITLE_AUTO",
    "title_similarity",
    "score_suspected_duplicate",
    "find_suspected_duplicate",
    "auto_merge_eligible",
    "find_auto_merge",
    "stamp_is_stale",
    "has_stale_auto_merge_stamp",
    "apply_auto_merges",
    "flag_suspected_duplicates",
]
