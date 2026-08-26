#!/usr/bin/env python3
"""
commitment_cluster — one line per real-world item, computed at render.

SPEC CLUSTER1 (2026-08-25). The queues group by CALL, so one real-world item
phrased across calls (or restated in one) renders as N rows, each asked about
separately. Live proof on the day of the spec: 5 deck rows were one
deliverable, 2 market rows were one, 2 entity-hygiene rows were one. The
WRITERS for folding those already exist and already work
(`commitment_state.supersede_commitment(user_confirmed=True)` +
`commitment_state.clear_review_flags`); what was missing was the SURFACE.

WHAT THIS MODULE IS
===================
  cluster_events      — PURE READ. Group a surface's rows into clusters of
                        "the same real-world item", at render time. A cluster
                        is a DISPLAY fact: nothing here writes.
  render_clusters     — the defensive wrapper every surface calls. Any
                        failure degrades to NO clusters (the surface renders
                        exactly as it did before this module existed) —
                        a broken clusterer must never take a queue down.
  cluster_index       — {survivor_id: cluster} / {folded_id: cluster} maps
                        for the render overlays.
  apply_cluster_merge — the ONE tap ("keep as one") made durable, THROUGH THE
                        EXISTING WRITERS ONLY: `clear_review_flags` on a
                        pending survivor + one `supersede_commitment(
                        user_confirmed=True)` per folded row. No new event
                        type, no new closure path, and every write stamped
                        into ONE `clu_` brain batch so a single `undo`
                        reverses the whole gesture (`brain_undo`
                        `commitment_merge` + `commitment_confirm` classes).

THE DOCTRINE (SPEC §0, binding)
===============================
1. Render-level clustering is DEFAULT-ON for every workspace. Not a setting
   a client must find. `CLUSTERING_ENABLED` below is a code-level constant
   for the mutation suite, NOT a knob: no skill names it, nothing offers to
   set it, and no workspace config reaches it.
2. A cluster is a DISPLAY fact until confirmed; a merge is a WRITE fact only
   on a user action. The floor doctrine stands — merging IS the adjudication
   of a suspected duplicate, so the write path requires the explicit gesture
   (`user_confirmed=True`, ids widget-embedded per the CLOSEID2 "id" door).
   Expanding, ignoring, or answering rows individually leaves history
   untouched.
3. Auto-merge stays confined to `commitment_dedup.auto_merge_eligible`,
   byte-identical. This module never sets the auto-merge flag, never writes
   an `auto_merge` stamp, and proposes only — the system deciding what you
   don't need to see must stay inspectable.
4. Precision over recall. A false split costs a duplicate line; a false join
   costs trust. Every join requires ALL of:
     - the shipped duplicate scorer's corroborated verdict
       (`commitment_dedup.score_suspected_duplicate` — shared content
       tokens, owner/counterparty gates; NOTHING here re-derives a
       similarity metric);
     - a shared counterparty/org read off the ROSTER, identity overlap and
       never a token guess (`commitment_backlog_sweep._shares_a_counterparty`
       — the sweep's own title-echo answer, one implementation);
     - temporal adjacency: the two captures landed within
       `CLUSTER_WINDOW_DAYS` of EACH OTHER (pairwise — not "recent vs now",
       which is the scorer's own window and a different question);
     - a title score at or above `CLUSTER_TITLE_FLOOR` (the scorer's
       corroborated bar, restated here so the mutation suite can zero it
       by name and watch the false-join control go red).

Pure read throughout except `apply_cluster_merge`, which writes only through
the two existing writers. Stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# DEFAULT-ON (SPEC §0-1). A constant, not a setting: the mutation suite flips
# it to prove the pins are live (`clusterer disabled -> the one-line pin goes
# red by name`); nothing else may.
CLUSTERING_ENABLED = True

# Temporal adjacency, PAIRWISE — two captures more than this many days apart
# are two different asks until a human says otherwise. Deliberately the flag
# tier's own window (commitment_dedup.DUP_WINDOW_DAYS == 14): the same
# recency intuition, applied between the rows instead of against the clock.
CLUSTER_WINDOW_DAYS = 14

# The join floor on the scorer's title score. Equals the scorer's
# corroborated bar (DUP_TITLE_STRONG) — restated as this module's own named
# constant so the mutation suite can zero THE JOIN THRESHOLD by name and
# assert the pinned false-join control notices.
CLUSTER_TITLE_FLOOR = 0.7

# The one tap (SPEC §0-2). Wire id; registered in `verb_taxonomy`, dispatched
# by apply-choices through `apply_cluster_merge` with the ids the widget row
# itself embeds (`data.id` + `data.folded_ids` — the CLOSEID2 "id" door).
CLUSTER_ACTION = "keep as one"

# The undo contract: one gesture = one `clu_` batch; `brain_undo.undo_batch`
# reverses every stamped write in it. Same shape as the sweep's `swb_`.
BATCH_PREFIX = "clu_"
BATCH_SALT_BYTES = 4

# The note the survivor's confirm carries — the user's gesture, in words.
SURVIVOR_CONFIRM_NOTE = ("kept as one — the cluster's surviving row, "
                         "confirmed by your tap")

# REVIEW_PR62 F-1 — the conflicting-deliverable fence. The scorer's
# corroborated 0.8 is a SIMILARITY verdict, and two near-template titles that
# differ on exactly the word that names the work (onboarding vs OFFboarding
# checklist, pricing vs HIRING proposal, quarterly vs INCIDENT review) sail
# over it while being two different real-world items. The tell is symmetric:
# EACH side carries a distinctive content word the other lacks. A true
# restatement differs only in function words and delivery verbs ("send the
# pricing deck" / "get the pricing deck over to me"), so those are noise
# here — excluded from the distinctive set, NOT from the join. The fence
# fails toward a false split (one duplicate line, the accepted cost per
# SPEC §0-5) and never toward a false join.
_CLUSTER_TOKEN_NOISE = frozenset((
    # function words the scorer's own similarity already discounts
    "the", "a", "an", "and", "or", "to", "for", "of", "on", "in", "with",
    "at", "by", "from", "into", "over", "up", "out", "off", "that", "this",
    "his", "her", "their", "our", "your", "my", "me", "us", "it", "before",
    "after", "about", "per", "via", "one", "two", "new",
    "is", "be", "as", "so", "no", "if", "go", "we", "re",
    # delivery/handling verbs — the vocabulary of RESTATING the same ask
    # (the recommit lexicon's verbs, plus the openers surfaces add)
    "send", "give", "get", "put", "pull", "share", "shoot", "draft",
    "write", "make", "do", "have", "circulate", "deliver", "forward",
    "schedule", "book", "email", "text", "follow", "confirm", "finish",
    "hold", "keep", "set", "lock", "save", "grab", "shot",
))


def _distinctive_tokens(title: str) -> set:
    """Content words that NAME the work — lowercase alnum tokens minus the
    noise vocabulary.

    REVIEW_PR62 R-1: short tokens DO distinguish work. "q3 vs q4 revenue
    report" is the fence's own thesis in two characters, and "phase 2 vs
    phase 3" differs on a single digit — so a digit-bearing token is
    distinctive at ANY length, and a two-letter alpha token (hr, pr, qa)
    is distinctive unless it is noise vocabulary. Only single-letter pure
    alpha tokens never distinguish. RECORDED RESIDUE, ruled not covered:
    "it" as a department name — the pronoun reading dominates title text
    ("send it over"), so "it" stays on the noise list and an hr-vs-IT pair
    joins; covering it would false-split ordinary restatements, the worse
    direction."""
    import re as _re
    out = set()
    for t in _re.findall(r"[a-z0-9]+", (title or "").lower()):
        if t in _CLUSTER_TOKEN_NOISE:
            continue
        if len(t) == 1 and t.isalpha():
            continue
        out.add(t)
    return out


def _conflicting_deliverables(title_a: str, title_b: str) -> bool:
    """True when BOTH titles carry a distinctive word the other lacks —
    the symmetric-difference tell of two different items in one template."""
    ta, tb = _distinctive_tokens(title_a), _distinctive_tokens(title_b)
    return bool(ta - tb) and bool(tb - ta)


def _cid(ev) -> str:
    from cru_match import _commitment_id

    return _commitment_id(ev)


def _title(ev) -> str:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    return str(d.get("title") or d.get("summary") or "")


def _when(ev) -> Optional[_dt.datetime]:
    from event_time import event_time, parse_ts

    return parse_ts(event_time(ev))


def _shares_counterparty(a, b, *, workspace_root=None) -> bool:
    """The roster conjunct — ONE implementation, the sweep's (its docstring
    is the argument; forking it here would let two surfaces disagree about
    whether a pair shares a counterparty)."""
    from commitment_backlog_sweep import _shares_a_counterparty

    return _shares_a_counterparty(a, b, workspace_root=workspace_root)


def cluster_events(events, *, workspace_root=None, now_iso=None,
                   window_days: Optional[int] = None) -> list:
    """Clusters of "the same real-world item" over a surface's own rows.

    PURE READ over the supplied events (commitment / capture event dicts —
    the same shapes the queue and triage surfaces already hold in hand).
    Returns a list of clusters, oldest survivor first::

        [{"cluster_id": "clu:<survivor_id>",
          "survivor_id": str,          # the OLDEST member — deterministic
          "member_ids": [str, ...],    # oldest first, survivor included
          "folded_ids": [str, ...],    # member_ids minus the survivor
          "n_folded": int,
          "scores": {folded_id: float, ...}}, ...]

    and [] when nothing joins — which is the answer for MOST renders, and the
    drop-empty / byte-identical contract every surface pins.

    Joins are transitive (a-b and b-c put a, b, c in one cluster — one item
    restated across three captures IS one item), and every individual join
    passed all four bars in the module docstring. `now_iso` is accepted for
    signature symmetry with the surfaces but unused: adjacency is between
    the rows, never against the clock.
    """
    if not CLUSTERING_ENABLED:
        return []
    window = CLUSTER_WINDOW_DAYS if window_days is None else int(window_days)
    max_gap = _dt.timedelta(days=window)

    from commitment_dedup import _person_name_index, score_suspected_duplicate

    items = [ev for ev in (events or [])
             if isinstance(ev, dict) and _cid(ev)]
    if len(items) < 2:
        return []
    # Oldest first — the survivor of a cluster is the OLDEST item, so a
    # deterministic age order makes the proposal reproducible run to run
    # (the sweep's own rule for its merge groups).
    _far_future = _dt.datetime.max.replace(tzinfo=_dt.timezone.utc)
    items.sort(key=lambda ev: (_when(ev) or _far_future, _cid(ev)))

    name_index = _person_name_index(workspace_root) if workspace_root else {}

    # Candidate pre-bucket: only pairs sharing a roster identity are ever
    # scored (the conjunct is REQUIRED anyway, and it keeps the render-time
    # cost near-linear instead of N^2 scorer calls on a long queue).
    from commitment_backlog_sweep import _party_keys
    keys_of: list = []
    for ev in items:
        try:
            keys_of.append(_party_keys(ev, workspace_root=workspace_root))
        except Exception:
            keys_of.append(set())

    parent: dict = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    scores: dict = {}
    for i, older in enumerate(items):
        parent.setdefault(_cid(older), _cid(older))
        t_older = _when(older)
        for j in range(i + 1, len(items)):
            newer = items[j]
            parent.setdefault(_cid(newer), _cid(newer))
            if not (keys_of[i] and keys_of[j] and (keys_of[i] & keys_of[j])):
                continue
            # Temporal adjacency, PAIRWISE. An unparseable timestamp on
            # either side refuses the join — precision over recall: absence
            # of evidence about WHEN is not adjacency.
            t_newer = _when(newer)
            if t_older is None or t_newer is None:
                continue
            if abs(t_newer - t_older) > max_gap:
                continue
            # The shipped scorer, with its own recency window OFF
            # (now_dt=None): recency-vs-now is its question, adjacency
            # between the rows is ours and was answered above.
            hit = score_suspected_duplicate(
                (newer.get("data") or {}), older,
                name_index=name_index, now_dt=None,
                workspace_root=workspace_root)
            if not hit or not hit.get("corroborated"):
                continue
            if hit["score"] < CLUSTER_TITLE_FLOOR:
                continue
            if not _shares_counterparty(older, newer,
                                        workspace_root=workspace_root):
                continue
            # REVIEW_PR62 F-1 — two near-template titles that each carry a
            # distinctive word the other lacks are two different items,
            # whatever the similarity score says.
            if _conflicting_deliverables(_title(older), _title(newer)):
                continue
            scores[(_cid(older), _cid(newer))] = hit["score"]
            union(_cid(older), _cid(newer))

    by_root: dict = {}
    for ev in items:
        by_root.setdefault(find(_cid(ev)), []).append(ev)
    clusters: list = []
    for members in by_root.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda ev: (_when(ev) or _far_future, _cid(ev)))
        survivor = members[0]
        sid = _cid(survivor)
        folded = [_cid(m) for m in members[1:]]
        clusters.append({
            "cluster_id": f"clu:{sid}",
            "survivor_id": sid,
            "member_ids": [sid] + folded,
            "folded_ids": folded,
            "n_folded": len(folded),
            "scores": {fid: scores.get((sid, fid))
                       for fid in folded},
        })
    clusters.sort(key=lambda c: c["survivor_id"])
    return clusters


def render_clusters(events, *, workspace_root=None, now_iso=None) -> list:
    """`cluster_events`, DEFENSIVE — the call every render path makes.

    Any failure yields NO clusters and the surface renders exactly as it did
    before CLUSTER1 (the byte-identical contract is also the degradation
    contract). The error is noted on stderr, never raised into a render."""
    try:
        return cluster_events(events, workspace_root=workspace_root,
                              now_iso=now_iso)
    except Exception as exc:  # pragma: no cover — the queue must still render
        sys.stderr.write(f"[commitment_cluster] clustering skipped: {exc}\n")
        return []


def cluster_index(clusters) -> tuple:
    """`(by_survivor, by_folded)` id maps for the render overlays."""
    by_survivor: dict = {}
    by_folded: dict = {}
    for c in clusters or []:
        by_survivor[c["survivor_id"]] = c
        for fid in c["folded_ids"]:
            by_folded[fid] = c
    return by_survivor, by_folded


def n_folded_total(clusters) -> int:
    """Total rows folded away — `information count = row count - this`."""
    return sum(int(c.get("n_folded") or 0) for c in clusters or [])


def information_count(total_rows: int, clusters) -> int:
    """The headline number (SPEC §0-4): clusters, not fragments. A clustered
    queue of 41 rows in 9 clusters says 9 — one line per cluster plus one
    per unclustered row."""
    return max(0, int(total_rows) - n_folded_total(clusters))


def folded_line(display_n, title: str, group_label: str = "") -> str:
    """ONE folded row as the read-only expand line both text and widget
    renders show — number kept, so the row stays REACHABLE and individually
    answerable even while folded (SPEC §0-2: answering rows individually
    leaves history untouched, and it must stay possible)."""
    core = f"{display_n}. {title}" if display_n is not None else str(title)
    return f"{core} — {group_label}" if group_label else core


def _mint_batch_id(now_iso=None) -> str:
    """`clu_<UTC-to-second>-<8 hex>` — the sweep's F-5/F-9 mint, same shape,
    own prefix. One human gesture = one batch = one `undo`."""
    import secrets

    from event_time import parse_ts

    now = (parse_ts(str(now_iso)) if now_iso else None) \
        or _dt.datetime.now(_dt.timezone.utc)
    stamp = now.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{BATCH_PREFIX}{stamp}-{secrets.token_hex(BATCH_SALT_BYTES)}"


def apply_cluster_merge(workspace_root, survivor_id, folded_ids, *,
                        merged_by: str, source_skill: str,
                        evidence: str = "", source_ref=None,
                        now_iso=None) -> dict:
    """The "keep as one" tap, made durable — EXISTING WRITERS ONLY.

    Two writes per gesture, both shipped paths, nothing new:

      1. `commitment_state.clear_review_flags` on the SURVIVOR — only when it
         is itself a pending-review queue member (the tap is the user saying
         the surviving capture is real). Stamped `commitment_confirm` so the
         batch undo returns it to the queue.
      2. `commitment_state.supersede_commitment(user_confirmed=True)` per
         folded row — merging IS the adjudication, and the tap on ids the
         widget itself embedded is the explicit user action the floor
         requires (CLOSEID2 "id" door). Stamped `commitment_merge`, whose
         registered reverser reopens the absorbed item and puts the pair
         back on the flag tier.

    Every write carries ONE freshly minted `clu_` `brain_batch_id`, so a
    single `undo` (`brain_undo.undo_batch` on the returned `batch_ref`)
    reverses the whole gesture. Never the auto carve-out flag — that
    belongs to `commitment_dedup.auto_merge_eligible` alone (SPEC §0-3).

    Statuses honored, never papered over: an already-closed folded row is an
    honest `already_resolved` no-op; a bad id is a per-row failure that does
    not abort the rest of the batch.

    Returns {"status", "batch_id", "batch_ref", "n_merged", "n_confirmed",
             "n_already", "n_failed", "results", "summary"}.
    """
    from commitment_state import (AmbiguousTargetError, CommitmentIdError,
                                  PendingReviewError, clear_review_flags,
                                  supersede_commitment)

    survivor = str(survivor_id or "").strip()
    folds = [str(f).strip() for f in (folded_ids or [])
             if str(f).strip() and str(f).strip() != survivor]
    if not survivor:
        raise ValueError("keep as one needs the cluster's survivor id — the "
                         "widget row embeds it (data.id)")
    if not folds:
        raise ValueError("keep as one needs the folded ids — the widget row "
                         "embeds them (data.folded_ids); a cluster of one is "
                         "not a merge")

    batch_id = _mint_batch_id(now_iso)
    results: list = []
    n_merged = n_already = n_failed = 0
    n_confirmed = 0

    # 1. The survivor's confirm — only when it is a queue member. Read
    # through the queue's own loader (the one definition of membership).
    survivor_pending = False
    try:
        from cru_match import load_needs_review
        events_path = str(Path(workspace_root) / "_hq" / "data"
                          / "events.jsonl")
        pending_ids = {_cid(ev) for ev in load_needs_review(
            events_path, workspace_root=str(workspace_root))}
        survivor_pending = survivor in pending_ids
    except Exception:
        survivor_pending = False
    if survivor_pending:
        try:
            res = clear_review_flags(
                workspace_root, survivor, cleared_by=merged_by,
                source_skill=source_skill, note=SURVIVOR_CONFIRM_NOTE,
                brain_batch_id=batch_id,
                brain_change_class="commitment_confirm")
            if res.get("status") == "cleared":
                n_confirmed += 1
            results.append({"commitment_id": survivor,
                            "role": "survivor",
                            "status": res.get("status")})
        except CommitmentIdError as exc:
            results.append({"commitment_id": survivor, "role": "survivor",
                            "status": "failed", "detail": str(exc)})
            n_failed += 1

    # 2. One supersede per folded row — the details ride the evidence.
    for fid in folds:
        detail = (evidence or
                  (f"kept as one from {source_skill} — same real-world item "
                   f"as {survivor} (shared wording, shared counterparty, "
                   f"captured within {CLUSTER_WINDOW_DAYS} days)"))
        try:
            res = supersede_commitment(
                workspace_root, survivor, fid,
                merged_by=merged_by, source_skill=source_skill,
                evidence=detail, user_confirmed=True,
                brain_batch_id=batch_id,
                brain_change_class="commitment_merge",
                source_ref=source_ref)
        except (CommitmentIdError, PendingReviewError,
                AmbiguousTargetError, ValueError) as exc:
            results.append({"commitment_id": fid, "role": "folded",
                            "status": "failed", "detail": str(exc)})
            n_failed += 1
            continue
        status = res.get("status")
        if status == "superseded":
            n_merged += 1
        elif status == "already_resolved":
            n_already += 1
        results.append({"commitment_id": fid, "role": "folded",
                        "status": status})

    if n_merged or n_confirmed:
        status = "merged"
    elif n_already and not n_failed:
        status = "already_resolved"
    else:
        status = "error"
    noun = "row" if n_merged == 1 else "rows"
    summary = (f"Kept as one — {n_merged} {noun} folded into the surviving "
               f"item. Say `undo` to split them back out.")
    if n_already:
        summary += f" {n_already} already settled — left alone."
    if n_failed:
        summary += f" {n_failed} could not be folded — details per row."
    return {
        "status": status,
        "batch_id": batch_id,
        "batch_ref": {"kind": "brain_batch", "batch_id": batch_id},
        "n_merged": n_merged,
        "n_confirmed": n_confirmed,
        "n_already": n_already,
        "n_failed": n_failed,
        "results": results,
        "summary": summary,
    }


__all__ = [
    "CLUSTERING_ENABLED", "CLUSTER_WINDOW_DAYS", "CLUSTER_TITLE_FLOOR",
    "CLUSTER_ACTION", "BATCH_PREFIX", "BATCH_SALT_BYTES",
    "cluster_events", "render_clusters", "cluster_index",
    "n_folded_total", "information_count", "folded_line",
    "apply_cluster_merge",
]
