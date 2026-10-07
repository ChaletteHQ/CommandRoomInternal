#!/usr/bin/env python3
"""
source_ref dedup index (SPEC A3) — O(1) membership set replacing "scan the last
200 events for a matching hash".

An active workspace emits 200 events in well under a week, so the last-200 scan
silently re-captures any duplicate older than the window (re-processing a
month-old transcript or a Drive backfill re-captured everything). This sidecar
index at `_hq/data/.source_refs.idx` is a plain-text membership set — one key
per line, two namespaces — that is O(1) to check, append-maintained inside the
A1 writer lock by `atomic_append_jsonl`, rebuildable from events.jsonl, and
verified weekly by cleanup.

Key namespaces (absorbs the documented hash drift — skills disagree on which
value they compute, so we index all three):
  - `h:<dedup_hash>`  the PASSIVE_CAPTURE 12-hex hash (`data.dedup_hash` or the
                      top-level `source_ref_hash` in the WORKSPACE_API shape)
  - `r:<sha256(dedup-key form of source_ref)[:16]>`  the `data.source_ref`
                      string. SPEC PROV2: stored pointers preserve the native
                      id's case, so this namespace keys on the ref's IDENTITY
                      form — derived through Layer A4, never a local fold —
                      and a case-variant re-observation of an already-appended
                      artifact still dedups instead of double-appending.
  - `c:<canonical_dedup_key>`  (R15/H-K, connector-agnostic-v1) the
                      provider:native_id canonical key from
                      connector_adapters.provenance — bridges a legacy
                      `gmail:<id>` string row and a NEW structured
                      `{provider,native_id}` provenance so a post-migration
                      re-observation of the SAME artifact reduces to one item.
                      Added additively (the r:/h: keys stay byte-stable so an
                      on-disk index from a prior release is never invalidated —
                      `_norm_source_ref`/`_r_key` are unchanged; the canonical
                      normalization is the new c: path, not a mutation of the
                      existing one).

The index is a CACHE over events.jsonl (the source of truth); corruption
self-heals via `rebuild`. Index writes must NEVER fail an event append.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Optional, Set

_IDX_NAME = ".source_refs.idx"


def _data_dir(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data"


def _idx_path(workspace_root) -> Path:
    return _data_dir(workspace_root) / _IDX_NAME


def _events_path(workspace_root) -> Path:
    return _data_dir(workspace_root) / "events.jsonl"


def _dedup_key_of():
    """Layer A4's identity derivation, or None when it cannot be imported."""
    try:
        from connector_adapters.provenance import dedup_key_of
        return dedup_key_of
    except Exception:
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            from connector_adapters.provenance import dedup_key_of
            return dedup_key_of
        except Exception:
            return None


def _dedup_form(source_ref) -> str:
    """The identity form of a STORED source pointer — SPEC PROV2 §3.

    THE ONE FOLD. Both namespaces route through Layer A4's `dedup_key_of`, so
    the index has exactly one case-fold and it lives where identity is defined.
    There is deliberately NO local `.lower()` fallback: a second, independent
    fold here would make the index dedup case-variants by LUCK rather than by
    derivation, and a fence that removed the derivation would stay green while
    the property it pins had gone. When Layer A4 cannot be imported the index
    degrades to the raw namespace — which is the same state the `c:` namespace
    is already in on that failure, since `_canonical_key` returns None there.

    Never raises: this runs inside `atomic_append`'s writer lock, and an index
    hiccup must never fail an event write."""
    fn = _dedup_key_of()
    if fn is None:
        return (source_ref or "").strip()
    try:
        return fn(source_ref) or (source_ref or "").strip()
    except Exception:
        return (source_ref or "").strip()


def _norm_source_ref(s: str) -> str:
    """The `r:` namespace's key material.

    PROV2 moved this from a local lowercase to the DEDUP-KEY form. For every
    already-lowercase spelling — which is every `r:` key an index on disk can
    contain for gmail / granola / session / outlook / drive rows — the bytes
    are unchanged, so a prior release's index is not invalidated. The two forms
    diverge only where Layer A4 REDUCES rather than folds: a Slack permalink or
    triple, and `gcalendar:` → `gcal:`. Those rows' old `r:` keys go stale
    until cleanup's `verify` → `rebuild` pass, and the `c:` namespace (whose
    value is unchanged) covers them meanwhile, so no duplicate can slip through
    the transition."""
    return _dedup_form(s)


def _r_key(source_ref: str) -> str:
    return "r:" + hashlib.sha256(_norm_source_ref(source_ref).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# CAPTUREONCE1 — the `t:` and `p:` namespaces (2026-09-14, M's ruling R4).
# --------------------------------------------------------------------------
#
# The `r:`/`h:`/`c:` namespaces answer "has this ARTIFACT been seen". That is
# the wrong question for a capture writer: one transcript legitimately yields
# eight commitments, so an artifact-level hit cannot be a write refusal. The
# question a capture writer has to ask is "have I already written THIS ROW off
# this artifact" — `(source_ref, title)`, the dedup key `scan-for-commitments`
# has claimed in prose since v4.6 and no code ever computed on the meeting leg.
#
#   `t:<sha256(dedup-form(source_ref) + NUL + title-key)[:16]>`
#         one per capture ROW. Title-key is the same rule the three mail/chat
#         legs already use by hand (`_title_key`: casefolded, first 60 chars),
#         so the index agrees with `inbound_capture.already_captured` /
#         `slack_capture.already_captured` / `sent_capture.already_captured`
#         on every exact re-capture instead of inventing a fourth spelling.
#   `p:<sha256(dedup-form(source_ref))[:16]>`
#         one per `meeting_processed` receipt. This is `meeting_capture.
#         already_processed` made O(1): the receipt predicate used to cost a
#         full-history scan, which is why nothing on the write path could
#         afford to consult it, which is why a re-run wrote 13 duplicate rows.
#
# THE INDEX FORMAT IS VERSIONED because of these two. An index written by an
# earlier release carries no `t:`/`p:` lines, and a missing key is a MISS —
# which on this path means "not a duplicate, write it". Silently degrading to
# today's behaviour would be a fence that is green and absent at the same
# time, so `check` refuses to answer a `t:`/`p:` question off a v1 file: it
# rebuilds first (the index is a cache; events.jsonl is truth).
_IDX_VERSION = 2
_IDX_VERSION_MARKER = f"#v{_IDX_VERSION}"
_TITLE_DEDUP_CHARS = 60

# The event types a capture writer produces. A `t:` key is only minted for
# these: a receipt or a closure is not a capture row and must never make the
# row it refers to look already-written.
#
# THE SET IS THE RE-RUN REFUSAL SET (review F-5, 2026-09-14). It started as
# the three row types §2.1 names plus the observed tier, and that was narrower
# than the rows a re-processed meeting actually produces: on M's own book the
# Sep 3 call's re-processings carry `person_proposal`, `commitment_to_discuss`
# and `interaction` rows, and a probe at the chokepoint wrote every one of
# them on a re-run while refusing the four below them. R4 is literal — "a
# meeting processed once writes NO new rows on a re-run" — so a row type a
# re-run produces belongs here. `objective_review` is deliberately NOT here:
# it is a periodic review record keyed on an objective, not a row extracted
# from the transcript, and refusing it would retire a cadence rather than a
# duplicate. Binding and correcting are untouched either way: they declare
# lineage (see LINEAGE_FIELDS) or are not capture types at all.
CAPTURE_TYPES = ("commitment", "commitment_observed", "decision", "meeting",
                 "person_proposal", "person_update_proposal",
                 "commitment_to_discuss", "interaction")

PROCESSED_RECEIPT_TYPE = "meeting_processed"

# A row that DECLARES it is a second write of a row already on the ledger is
# not a re-capture and must never be refused as one. The observed tier's
# promotion (`promoted_from`) is the live case: a corroborated `commitment_
# observed` is re-written as a real `commitment` with the same source_ref and
# the same title on purpose, and refusing it would silently retire the whole
# corroboration path. `promoted_from_observed` / `supersedes` / `corrects` /
# `correction_of` / `rebound_from` are the same shape of claim. The test is
# the row's own stated lineage, not a guess: a row with no lineage field is a
# fresh capture.
#
# `duplicate_of` IS DELIBERATELY NOT ON THIS LIST (2026-09-14, found by this
# lane's own end-to-end pin). It is not a claim of sanctioned second writing —
# it is `commitment_dedup.flag_suspected_duplicates` stamping "this row is a
# twin of one already on your plate" as it converts the row to the observed
# tier, INSIDE the same append, one step before this filter runs. Treating
# that as lineage exempted exactly the rows the lane exists to refuse: a
# re-processed meeting still landed one `commitment_observed` per commitment,
# reason "the same ask is already on your plate", on every re-run. A row whose
# own data says it duplicates another row is the definition of a row a re-run
# must not write.
# `split_from` (night 11b trial merge, merged-tree review): a child minted by
# `commitment_state.split_commitment` inherits its parent's `source_ref` and
# is the customer's own correction, never a re-run of the capture.
LINEAGE_FIELDS = ("promoted_from", "promoted_from_observed",
                  "supersedes", "corrects", "correction_of", "rebound_from",
                  "split_from")


def declares_lineage(event: dict) -> bool:
    """True when the row says it is a second write of an existing row."""
    if not isinstance(event, dict):
        return False
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return any(data.get(f) for f in LINEAGE_FIELDS)


def _title_key(title) -> str:
    return (str(title or "").strip().lower())[:_TITLE_DEDUP_CHARS]


def capture_title_of(event: dict) -> str:
    """The row title a capture event dedups on, across the shapes in the tree:
    `data.title` (commitments, meetings) or `data.summary` (decisions)."""
    if not isinstance(event, dict):
        return ""
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    key = _title_key(data.get("title") or data.get("summary"))
    if key and str(event.get("type") or "") == "interaction":
        # night 11b trial merge (merged-tree review): a chat touch's identity
        # is the ENTITY it touched, carried top-level (`person_ids`/`org_ids`),
        # and its summary is a function of the mention count only - two
        # people named in one message would otherwise share one key and the
        # second would be refused as a duplicate. Key-only; never rendered.
        ids = sorted(str(x) for k in ("person_ids", "org_ids")
                     for x in (event.get(k) or []) if x)
        if ids:
            key = key + "|" + ",".join(ids)
    return key


def _t_key(source_ref: str, title) -> str:
    tk = _title_key(title)
    if not tk or not str(source_ref or "").strip():
        return ""
    material = _norm_source_ref(source_ref) + "\x00" + tk
    return "t:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _p_key(source_ref: str) -> str:
    if not str(source_ref or "").strip():
        return ""
    return "p:" + hashlib.sha256(
        _norm_source_ref(source_ref).encode("utf-8")).hexdigest()[:16]


def _keys_of(event: dict) -> Set[str]:
    """Extract the `h:`/`r:`/`c:`/`t:`/`p:` keys from an event across all
    observed shapes.
    Defensive — never raises; returns an empty set for a shapeless event."""
    keys: Set[str] = set()
    if not isinstance(event, dict):
        return keys
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    dedup_hash = (
        data.get("dedup_hash")
        or event.get("dedup_hash")
        or event.get("source_ref_hash")
        or data.get("source_ref_hash")
    )
    if dedup_hash:
        keys.add("h:" + str(dedup_hash))
    source_ref = data.get("source_ref") or event.get("source_ref")
    if isinstance(source_ref, str) and source_ref.strip():
        keys.add(_r_key(source_ref))
    # c: canonical dedup key (R15/H-K) — bridges legacy string provenance and
    # the new structured {provider,native_id} form. Best-effort; never raises
    # (this runs inside atomic_append's writer lock — an index hiccup must
    # never fail an event write).
    ck = _canonical_key(event)
    if ck:
        keys.add("c:" + ck)
    # CAPTUREONCE1 — the row-level and receipt-level keys. Both derive from
    # the SAME `source_ref` the `r:` key above uses, so a workspace whose
    # index is rebuilt gets them for its whole history with no migration.
    etype = event.get("type")
    ref_for_row = source_ref if isinstance(source_ref, str) else None
    if etype in CAPTURE_TYPES and ref_for_row:
        tk = _t_key(ref_for_row, data.get("title") or data.get("summary"))
        if tk:
            keys.add(tk)
    if etype == PROCESSED_RECEIPT_TYPE:
        # A receipt's identity can be on `data.source_ref` OR `data.meeting_id`
        # (the bare-id vs `granola:`-prefixed drift `meeting_capture.
        # _norm_ref_keys` already absorbs). Index BOTH spellings so a caller
        # holding either one gets the same answer.
        for ref in (ref_for_row, data.get("meeting_id")):
            if not isinstance(ref, str) or not ref.strip():
                continue
            pk = _p_key(ref)
            if pk:
                keys.add(pk)
            if ":" not in ref:
                pk2 = _p_key("granola:" + ref)
                if pk2:
                    keys.add(pk2)
    return keys


def _canonical_key(event: dict) -> Optional[str]:
    """The connector_adapters.provenance canonical key for an event, or None.
    Isolated + defensive so an import/parse failure degrades to h:/r: only."""
    try:
        from connector_adapters.provenance import canonical_dedup_key
    except Exception:
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            from connector_adapters.provenance import canonical_dedup_key
        except Exception:
            return None
    try:
        return canonical_dedup_key(event=event)
    except Exception:
        return None


def _load_idx(workspace_root) -> Set[str]:
    p = _idx_path(workspace_root)
    if not p.exists():
        return set()
    out: Set[str] = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        # CAPTUREONCE1 — `#`-led lines are format metadata, never keys. No key
        # namespace starts with `#`, so this cannot swallow one.
        if line and not line.startswith("#"):
            out.add(line)
    return out


def idx_version(workspace_root) -> int:
    """The format version stamped in the index file, or 1 for a file written
    before CAPTUREONCE1 (no marker). Used by `check` to refuse answering a
    `t:`/`p:` question off a file that cannot contain those keys."""
    p = _idx_path(workspace_root)
    if not p.exists():
        return 0
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("#v"):
                try:
                    return int(line[2:])
                except ValueError:
                    return 1
            if line:
                return 1
    except Exception:
        return 1
    return 1


def _history_paths(workspace_root) -> list:
    """EVERY ledger file this workspace's history lives in — the yearly
    shards A5 rotates out plus the active file — through `events_io`, the one
    shard-transparent reader. A tree without `events_io` falls back to the
    active file alone, which is what this module read before CAP-F5."""
    try:
        from events_io import shard_paths
    except ImportError:  # pragma: no cover — direct-path import
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            from events_io import shard_paths
        except Exception:  # pragma: no cover
            ep = _events_path(workspace_root)
            return [ep] if ep.exists() else []
    except Exception:  # pragma: no cover
        ep = _events_path(workspace_root)
        return [ep] if ep.exists() else []
    return [p for p in shard_paths(_data_dir(workspace_root)) if p.exists()]


def _has_history(workspace_root) -> bool:
    """True when this workspace has ANY ledger file — a rotated workspace
    whose active file has not been recreated yet still has history, and the
    old `events.jsonl.exists()` gate answered False for it."""
    return bool(_history_paths(workspace_root))


def _derive_keys(workspace_root) -> tuple[Set[str], int]:
    """Re-derive the full key set from the WHOLE ledger. Defensive
    line-by-line.

    CAP-F5 (night 11b's deferral, built by CARD1 2026-09-14). This read used
    to open `events.jsonl` by path. A5 rotates every prior calendar year into
    `events-<year>.jsonl` shards, so on any workspace old enough to have
    rotated, a rebuild derived keys from the CURRENT YEAR ONLY — and the
    index is what CAPTUREONCE1's `(source_ref, title)` refusal reads. A call
    processed last year answered MISS and its rows were written a second
    time: the capture-once gate silently defeated for exactly the history it
    was built to protect. The standing fence says every full-history read
    goes through `events_io`, and this is a full-history read.

    It routes through `events_io.shard_paths` — the module's shard-
    transparency contract — and keeps this function's own defensive parse
    rather than calling `iter_events`. Deliberate: `iter_events` admits only
    rows carrying a non-empty string `type`, so switching to it would
    silently change the event COUNT `rebuild` reports (its `{"events": n}`)
    on any ledger holding a typeless line. The shard set is the part that
    was wrong; the parse was not."""
    keys: Set[str] = set()
    n = 0
    for ep in _history_paths(workspace_root):
        for line in ep.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if not isinstance(ev, dict):
                continue
            n += 1
            keys |= _keys_of(ev)
    return keys, n


def rebuild(workspace_root) -> dict:
    """Rebuild the index from events.jsonl. Sorted + deduped → idempotent
    (byte-identical on a re-run).

    NOT UNDER THE HELPER DOOR (MF-M2-21, 2026-09-20). This file is a cache,
    and the rebuild is lazy: the first inbox capture on a workspace that has
    never captured finds no `.source_refs.idx` and mints one. Reached through
    `inbox_helpers:plan_inbound_capture` — an allow-listed READ — that put a
    new file inside the customer's own data directory, past both write verbs'
    fences, on a merged seat. The layer stamps `CR_HELPER_DOOR` on every
    helper child process; under it the keys are still DERIVED and the same
    answer is returned, so no caller learns anything different. The file is
    simply not minted by a read, and the caller's own `append_jsonl` rebuilds
    it in-process the moment there is something to write."""
    if os.environ.get("CR_HELPER_DOOR"):
        keys, n = _derive_keys(workspace_root)
        return {"events": n, "keys": len(keys), "version": _IDX_VERSION}
    keys, n = _derive_keys(workspace_root)
    p = _idx_path(workspace_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    # CAPTUREONCE1 — the version marker leads the file so `idx_version` reads
    # one line. Still sorted + deduped below it → still byte-identical on a
    # re-run.
    body = "\n".join(sorted(keys))
    p.write_text(_IDX_VERSION_MARKER + "\n" + body + ("\n" if keys else ""),
                 encoding="utf-8")
    return {"events": n, "keys": len(keys), "version": _IDX_VERSION}


def record_keys(workspace_root, events: Iterable[dict]) -> int:
    """Append any NEW keys from `events` to the index. Best-effort — called from
    inside `atomic_append_jsonl`'s writer-lock scope; never raises. Returns the
    count of keys added."""
    try:
        existing = _load_idx(workspace_root)
        new: Set[str] = set()
        for ev in events:
            new |= _keys_of(ev)
        add = new - existing
        if not add:
            return 0
        p = _idx_path(workspace_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        # CAPTUREONCE1 — a file this function CREATES is a v2 file and has to
        # say so. It mints `t:`/`p:` keys like any other write path, and an
        # unstamped file reads as v1, which sends every later `check(title=)`
        # and `check_processed` through a full rebuild — the O(1) write-path
        # answer this lane exists to provide, silently costing a full-history
        # scan per append. Only on CREATE: an existing v1 file is left alone
        # and `check` rebuilds it once, because stamping v2 on a file that has
        # no `t:` keys in it would claim an answer the file cannot give.
        fresh = not p.exists()
        if fresh and _has_history(workspace_root):
            # night 11b trial merge (merged-tree review F-3): a file this
            # function creates over a workspace that already HAS history
            # would carry one batch's keys under a v2 stamp, and every later
            # check would read that as the whole truth - CAPTUREONCE1 inert
            # on the whole of history, fence green. Rebuild from the ledger
            # first; the marker then says something true.
            try:
                rebuild(workspace_root)
            except Exception:  # pragma: no cover
                pass
            fresh = not p.exists()
        with p.open("a", encoding="utf-8") as f:
            if fresh:
                f.write(_IDX_VERSION_MARKER + "\n")
            for k in sorted(add):
                f.write(k + "\n")
        return len(add)
    except Exception:
        return 0


def _rebuild_if_v1(workspace_root) -> None:
    """THE UPGRADE FENCE (CAPTUREONCE1; pinned in fix round 1, review F-3).

    An index written before this build carries no `t:`/`p:` keys, so a
    `(source_ref, title)` or receipt question asked off it comes back a false
    MISS — a duplicate written because the index was old. That is the state
    EVERY existing workspace is in on its first re-run after the upgrade, so
    without this the whole lane would be inert on exactly the workspaces it
    was built for. Rebuild rather than degrade: the index is a cache,
    events.jsonl is truth.

    It lives here, once, rather than inline at both callers: duplicated, each
    copy hid the other's removal (the reviewer could delete either one and
    every suite stayed green), which is the definition of a fence that cannot
    be proven."""
    if (idx_version(workspace_root) < _IDX_VERSION
            and _has_history(workspace_root)):
        rebuild(workspace_root)


def check(workspace_root, source_ref: Optional[str] = None,
          dedup_hash: Optional[str] = None,
          title: Optional[str] = None) -> bool:
    """True if this source_ref / dedup_hash is already captured. Read-only and
    lock-free (a stale read at worst lets one duplicate through — same as the old
    race; capture must never block). Lazy-migrates: builds the index from
    events.jsonl on first check when the idx is missing.

    CAPTUREONCE1 — `title` asks the CAPTURE question instead of the artifact
    question: "is this `(source_ref, title)` ROW already on disk". It is a
    different question and it gets a different answer path — an artifact-level
    `r:` hit is NOT a capture hit, because one transcript legitimately yields
    many rows, and folding the two would refuse every commitment after the
    first. With a title this consults the `t:` namespace and nothing else."""
    if not _idx_path(workspace_root).exists() and _has_history(workspace_root):
        rebuild(workspace_root)
    if title is not None:
        _rebuild_if_v1(workspace_root)
        tk = _t_key(source_ref or "", title)
        return bool(tk) and tk in _load_idx(workspace_root)
    idx = _load_idx(workspace_root)
    if dedup_hash and ("h:" + str(dedup_hash)) in idx:
        return True
    if source_ref and _r_key(source_ref) in idx:
        return True
    # Canonical-key bridge (R15): a legacy source_ref string also hits a
    # structured re-observation of the same artifact (indexed under c:).
    if source_ref:
        ck = _canonical_key({"data": {"source_ref": source_ref}})
        if ck and ("c:" + ck) in idx:
            return True
    return False


def check_processed(workspace_root, source_ref: Optional[str] = None) -> bool:
    """True when a `meeting_processed` receipt for this source_ref is on disk.

    CAPTUREONCE1 — `meeting_capture.already_processed` in O(1). Same answer,
    same receipt type; the difference is that this one is cheap enough to run
    on the WRITE path, which is the whole reason a re-run could write 13
    duplicate rows while a predicate that would have refused them sat unused
    two modules away."""
    if not str(source_ref or "").strip():
        return False
    if not _idx_path(workspace_root).exists() and _has_history(workspace_root):
        rebuild(workspace_root)
    _rebuild_if_v1(workspace_root)
    idx = _load_idx(workspace_root)
    pk = _p_key(source_ref)
    if pk and pk in idx:
        return True
    ref = str(source_ref).strip()
    if ":" not in ref:
        pk2 = _p_key("granola:" + ref)
        return bool(pk2) and pk2 in idx
    # `granola:<id>` also answers for a receipt indexed under the bare id.
    bare = ref.split(":", 1)[1]
    pk3 = _p_key(bare) if bare else ""
    return bool(pk3) and pk3 in idx


def filter_capture_duplicates(workspace_root, events) -> dict:
    """THE CAPTURE CHOKEPOINT'S ANSWER (CAPTUREONCE1 §2.1 + §2.2, M's R4).

    Split a batch into the rows that may be written and the rows that must
    not, for the two reasons a capture row must never be written twice:

      `rerun`     the row's `source_ref` already carries a `meeting_processed`
                  receipt. A meeting processed once writes NO new commitment,
                  decision or meeting row on a re-run — re-runs may only bind
                  or correct what is already there, through the correction
                  writers. This is the 13-duplicate / 4-resurrected-rows
                  finding of the v5.30.0 attended test (B1.3).
      `on_disk`   the `(source_ref, title)` pair is already indexed. This is
                  the dedup key `scan-for-commitments` has promised in prose
                  since v4.6 and that no code computed on the meeting leg.

    Returns {"kept", "dropped", "n_rerun", "n_deduped_on_disk"}. `dropped`
    rows carry `_capture_dedup_reason` for the caller's receipt; they are
    COPIES, so the caller's own objects are never mutated.

    Scope, deliberately narrow: only `CAPTURE_TYPES` rows are ever considered,
    and only when they carry a `source_ref`. A receipt, a closure, a binding
    correction, an undo — none of them is a capture row, so none of them can
    be refused here. Never raises: on any internal failure the batch passes
    through unchanged, because a capture must never be LOST by the guard that
    exists to stop it being written twice."""
    events = list(events or [])
    out = {"kept": events, "dropped": [], "n_rerun": 0, "n_deduped_on_disk": 0}
    try:
        if not events:
            return out
        kept: list = []
        dropped: list = []
        seen_refs: dict = {}
        batch_titles: Set[str] = set()
        for ev in events:
            if not isinstance(ev, dict) or ev.get("type") not in CAPTURE_TYPES:
                kept.append(ev)
                continue
            if declares_lineage(ev):
                # A promotion / correction / rebind is the SANCTIONED second
                # write of a row. Refusing it here would retire the observed
                # tier's corroboration path without anything saying so.
                kept.append(ev)
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            ref = data.get("source_ref") or ev.get("source_ref")
            if not isinstance(ref, str) or not ref.strip():
                kept.append(ev)
                continue
            # G30 — the memo is keyed on the DERIVED form, never the raw
            # pointer. Two spellings of one transcript (`granola:x` and `x`)
            # are one question, and a raw key would ask it twice and let a
            # case-variant miss the memo entirely.
            memo_key = _dedup_form(ref)
            if memo_key not in seen_refs:
                seen_refs[memo_key] = check_processed(workspace_root, ref)
            if seen_refs[memo_key]:
                row = dict(ev)
                row["_capture_dedup_reason"] = "rerun"
                dropped.append(row)
                out["n_rerun"] += 1
                continue
            title = data.get("title") or data.get("summary")
            tk = _t_key(ref, title)
            if tk and (tk in batch_titles
                       or check(workspace_root, source_ref=ref, title=title)):
                row = dict(ev)
                row["_capture_dedup_reason"] = "on_disk"
                dropped.append(row)
                out["n_deduped_on_disk"] += 1
                continue
            if tk:
                batch_titles.add(tk)
            kept.append(ev)
        out["kept"] = kept
        out["dropped"] = dropped
        return out
    except Exception:
        return {"kept": events, "dropped": [], "n_rerun": 0,
                "n_deduped_on_disk": 0}


def verify(workspace_root) -> bool:
    """True iff the index exactly matches the keys derived from events.jsonl.
    cleanup rebuilds on mismatch (the index is a cache; events.jsonl is truth)."""
    derived, _ = _derive_keys(workspace_root)
    return derived == _load_idx(workspace_root)


__all__ = ["check", "check_processed", "filter_capture_duplicates",
           "capture_title_of", "idx_version", "CAPTURE_TYPES",
           "rebuild", "verify", "record_keys", "_keys_of"]


def _main(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="source_ref_index.py")
    ap.add_argument("cmd", choices=["check", "rebuild", "verify"])
    ap.add_argument("workspace_root")
    ap.add_argument("--source-ref")
    ap.add_argument("--dedup-hash")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        hit = check(a.workspace_root, source_ref=a.source_ref, dedup_hash=a.dedup_hash)
        print("HIT" if hit else "MISS")
        return 0 if hit else 1
    if a.cmd == "rebuild":
        print(json.dumps(rebuild(a.workspace_root)))
        return 0
    ok = verify(a.workspace_root)
    print("OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(_main(sys.argv[1:]))
