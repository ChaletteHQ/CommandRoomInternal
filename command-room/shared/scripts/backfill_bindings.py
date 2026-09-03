#!/usr/bin/env python3
"""BACKFILL1 — R3: the binding-backfill one-shot (SPEC_BACKFILL1, 2026-08-31).

Re-binds name-matched-unbound substance events to the threads whose work they
record — 100% PROPOSE-CONFIRM. There is NO auto-bind lane in this module, by
M's 2026-08-31 ruling (spec §0.1): every write requires an explicit accepted
seq from a human reading the evidence. The propose report doubles as the
hand-labeled precision dataset (§V) — every accept/reject is recorded with
its evidence tier in the run receipt, so precision-by-tier is computable
before any auto tier is ever considered (gate: >= 0.95 on the would-be-auto
tier, measured, or the maintenance job ships propose-only).

THE WORK LIST IS THE GAUGE'S OWN WALK (spec §M.1 — no second matching
implementation): candidates come from `binding_gauge.terms_regexes` +
`binding_gauge.match_name_unbound` + `binding_gauge.event_match_line` over
the SAME owner-scoped, reclassification-FOLDED event stream `build_gauge`
measures. A row this module proposes is by construction a row the gauge
counts in `name_unbound`; an accepted row moves from `name_unbound` to
`bound` and the denominator is invariant.

WRITE SHAPE (spec §0.3 — the existing `reclassification` rail, no new bind
type): each accepted row appends ONE `reclassification` event through the
event gate — `supersedes_seq` = the original event (one hop, never chained),
the corrected envelope, `data.origin: "backfilled"` (distinguishable origin,
forever), `data.bind_basis` (the evidence string), and the `bkf_` batch
stamp. Named consumers of the write: `binding_gauge.build_gauge` (folds
reclassifications before its walk — the seam landed with this build) and
every `honor_reclassifications=True` reader.

BOUND-ELSEWHERE rows (spec §0.4): the corrected envelope preserves
`primary_thread_id` UNTOUCHED and appends the target thread to
`related_thread_ids` — additive only, enforced by a separate validation pass
(`_validate_composed`) that refuses the whole batch if any composed write
would replace a bound-elsewhere row's primary. Every current thread ref is
lifted into the corrected envelope (the fold clears legacy data-level
spellings on the patched copy — a ref left behind would be ERASED from the
folded read).

SCOPE (spec §0.6): active + scoping threads by default. Paused / archived /
dormant / resolved threads are excluded — `--thread <id>` opts one in
explicitly.

BATCH + UNDO (the UNCONFEXP1 / `swb_` precedent): one apply = one freshly
minted `bkf_<UTC>-<8hex>` batch; every write carries
`data.brain_batch_id` + `data.brain_change_class: "binding_backfill"`, so
`brain_undo.undo_batch({"kind": "brain_batch", "batch_id": ...})` reverses
the sitting with one gesture (the registered reverser appends RESTORING
reclassifications — later seq wins the fold; history keeps both). The CLI
`undo` subcommand wraps that call and triggers ONE gauge rebuild after.

IDEMPOTENCY (spec §M.5): free by construction for accepts — the fold-first
walk means an applied row is no longer name-unbound, so a re-run proposes
nothing; the reclassification rows ARE the marker. Rejected rows are skipped
via the run receipts' `data.rejected` records (this module's own
`_rejected_pairs` reader — the receipt's named consumer).

RECEIPT: one `binding_backfill_run` event per apply — rows walked / proposed
/ applied / rejected (with tiers — the precision dataset) / refused-by-reason,
plus before/after READY counts (spec §0.7: the flip is visible in the same
gesture that caused it). `apply` triggers `binding_gauge.write_gauge`.

STALENESS FENCE (spec §M.2 / §R.4): `apply` requires `--snapshot`, the
`snapshot_max_seq` the propose report printed; if the substrate's human
high-water mark has moved, apply REFUSES and asks for a fresh propose —
term sets and candidate rows are only valid against the snapshot they were
derived from. Apply additionally re-derives fresh and acts only on rows
still applicable.

CLI:
    python backfill_bindings.py propose <workspace_root> [--thread ID]...
                                        [--json] [--now ISO]
    python backfill_bindings.py apply   <workspace_root> --snapshot N
                                        [--accept 12,34|all] [--reject 56,78]
                                        --applied-by NAME [--thread ID]...
                                        [--source-skill S] [--json] [--now ISO]
    python backfill_bindings.py undo    <workspace_root> --batch bkf_...
                                        --undone-by NAME [--json]

stdlib + sibling shared/scripts modules only.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import binding_gauge as bg  # noqa: E402
import event_refs  # noqa: E402
import events_io  # noqa: E402
import substance_events  # noqa: E402
from entities_io import entities_collection, unwrap_entities  # noqa: E402
from event_seq import event_seq  # noqa: E402
from thread_activity import apply_reclassifications  # noqa: E402

BATCH_PREFIX = "bkf_"
BATCH_SALT_BYTES = 4
CHANGE_CLASS = "binding_backfill"
RECEIPT_EVENT_TYPE = "binding_backfill_run"

# Spec §0.6 — the default work-list fence. A thread outside this set gets a
# row ONLY when `--thread <id>` names it explicitly.
SCOPE_STATUSES = frozenset({"active", "scoping"})

# Spec §0.5 corroborators, frozen at build.
TEMPORAL_WINDOW_DAYS = 3
TEMPORAL_MIN_NEIGHBORS = 2

PREVIEW_CHARS = 140

_PREVIEW_FIELDS = ("title", "summary", "text", "notes", "subject",
                   "description", "what", "content")


class BackfillGuardError(RuntimeError):
    """A composed write violated a fence — the WHOLE batch is refused,
    nothing is written (two-phase apply: compose, validate, then write)."""


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _now_iso(now_iso: Optional[str] = None) -> str:
    import datetime as _dt

    if now_iso:
        return now_iso
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _mint_batch_id(now_iso: Optional[str] = None) -> str:
    """`bkf_<UTC-to-second>-<8 hex>` — one confirmed sitting = one batch =
    one `undo` (the `swb_`/`thb_`/`exb_` mint shape, own prefix)."""
    import datetime as _dt
    import secrets

    if now_iso:
        base = _dt.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        if base.tzinfo is None:
            base = base.replace(tzinfo=_dt.timezone.utc)
    else:
        base = _dt.datetime.now(_dt.timezone.utc)
    stamp = base.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{BATCH_PREFIX}{stamp}-{secrets.token_hex(BATCH_SALT_BYTES)}"


def _batch_stamp(batch_id: str) -> dict:
    """Factored out so the mutation suite can drop the stamp BY NAME and
    watch the undo round-trip pin go red — same removal-proof shape as
    `exchange_backfill._batch_stamp` / `backfill_prep_briefs._batch_stamp`."""
    return {"brain_batch_id": batch_id, "brain_change_class": CHANGE_CLASS}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _event_dt(ev: dict):
    """Parsed UTC-aware event timestamp (all three live field spellings via
    event_time), naive stamps taken as UTC — the F-15 posture
    `thread_activity._ts_key` pins. None when unparseable."""
    import datetime as _dt

    try:
        from event_time import event_dt

        dt = event_dt(ev)
    except Exception:
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def _preview(ev: dict) -> str:
    """A short human-readable evidence line for the confirmer. Data-level
    text fields first (the payload is where the words live), else the
    top-level twins. Never raises; empty string when nothing readable."""
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for scope in (d, ev):
        for k in _PREVIEW_FIELDS:
            v = scope.get(k)
            if isinstance(v, str) and v.strip():
                s = " ".join(v.split())
                return s[:PREVIEW_CHARS] + ("…" if len(s) > PREVIEW_CHARS
                                            else "")
    return ""


def _current_primary(ev: dict) -> Optional[str]:
    """The folded event's primary thread ref: the canonical slot when set,
    else the gate's own derive ladder (`event_types.derive_primary_thread_id`
    — the ONE promotion rule, reused not re-derived)."""
    p = ev.get("primary_thread_id")
    if isinstance(p, str) and p.strip():
        return p.strip()
    try:
        from event_types import derive_primary_thread_id

        return derive_primary_thread_id(ev)
    except Exception:
        return None


def _source_ref_keys(ref) -> set:
    """Normalized source-ref family keys. `meeting_capture.meeting_ref_keys`
    (the product's one notion of "the same meeting") when it yields keys;
    exact lowercased string as the fallback family for non-meeting refs
    (mail thread ids etc. — reviewer-visible default: adjacency for those is
    exact-match, no fuzzier notion exists in the house)."""
    if not isinstance(ref, str) or not ref.strip():
        return set()
    try:
        from meeting_capture import meeting_ref_keys

        keys = meeting_ref_keys(ref)
        if keys:
            return set(keys)
    except Exception:
        pass
    return {ref.strip().lower()}


def _event_source_ref(ev: dict):
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    return d.get("source_ref") or ev.get("source_ref")


# ---------------------------------------------------------------------------
# Rejected-pair reader — the `binding_backfill_run` receipt's named consumer
# ---------------------------------------------------------------------------


def _rejected_pairs(events: list) -> set:
    """{(seq, thread_id)} every prior run's confirmer explicitly REJECTED.
    Rejections are recorded in the run receipt (`data.rejected`), never as
    per-row events (reviewer-visible default: one receipt per sitting is the
    house receipt discipline; 70 rejection events would be substrate noise
    with no reader beyond this one). Rejections survive `undo` — undo
    reverses WRITES; a recorded human "no" stands until a human re-rules."""
    out: set = set()
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != RECEIPT_EVENT_TYPE:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        for row in data.get("rejected") or []:
            if not isinstance(row, dict):
                continue
            seq = event_seq({"seq": row.get("seq")})
            tid = row.get("thread_id")
            if seq is not None and isinstance(tid, str) and tid:
                out.add((seq, tid))
    return out


# ---------------------------------------------------------------------------
# Corroborators (spec §0.5, frozen at build)
# ---------------------------------------------------------------------------


def _thread_context(folded: list, tids: set, entities: dict) -> dict:
    """Per-thread evidence context off the ALREADY-BOUND record: linked
    people (person ids seen on the thread's bound events + the record's
    `key_contact_id` — reviewer-visible default: the spec says "the thread's
    linked people" without pinning a source; bound-event people IS the
    observed linkage, the record field is the asserted one, and the union is
    the generous read for a corroborator that never acts alone), source-ref
    families of bound events, and timestamps of bound substance events (for
    the temporal-cluster test)."""
    email_idx = event_refs.email_person_index(entities)
    ctx = {tid: {"people": set(), "ref_keys": set(), "sub_ts": []}
           for tid in tids}
    for ev in folded:
        if not isinstance(ev, dict):
            continue
        refs = event_refs.threads_of(ev) & tids
        if not refs:
            continue
        people = event_refs.meeting_person_ids(ev, email_idx)
        rkeys = _source_ref_keys(_event_source_ref(ev))
        is_sub = substance_events.is_substance_event(ev)
        dt = _event_dt(ev) if is_sub else None
        for tid in refs:
            ctx[tid]["people"] |= people
            ctx[tid]["ref_keys"] |= rkeys
            if dt is not None:
                ctx[tid]["sub_ts"].append(dt)
    for t in entities_collection(entities, "threads"):
        tid = t.get("id")
        kc = t.get("key_contact_id")
        if tid in ctx and isinstance(kc, str) and kc.strip():
            ctx[tid]["people"].add(kc.strip())
    return ctx


def _org_thread_cache():
    cache: dict = {}

    def linked(workspace_root, org_id, entities) -> set:
        if org_id not in cache:
            try:
                from entity_resolve import linked_projects_for_org

                cache[org_id] = {p.get("id") for p in linked_projects_for_org(
                    workspace_root, org_id, entities=entities)
                    if p.get("id")}
            except Exception:
                cache[org_id] = set()
        return cache[org_id]

    return linked


def _corroborators(ev: dict, tid: str, ctx: dict, entities: dict,
                   workspace_root, org_linked, email_idx) -> list:
    """The spec §0.5 corroborator vector for (event, target thread). Names
    only — the tier table counts them; the receipt records them."""
    out: list = []
    people = event_refs.meeting_person_ids(ev, email_idx)
    tctx = ctx.get(tid) or {"people": set(), "ref_keys": set(), "sub_ts": []}
    if people & tctx["people"]:
        out.append("person_overlap")
    if people:
        try:
            from entity_resolve import person_org_ids

            orgs = set()
            for pid in people:
                orgs |= person_org_ids(entities, pid)
        except Exception:
            orgs = set()
        if any(tid in org_linked(workspace_root, o, entities) for o in orgs):
            out.append("org_link")
    rkeys = _source_ref_keys(_event_source_ref(ev))
    if rkeys & tctx["ref_keys"]:
        out.append("source_ref_adjacency")
    dt = _event_dt(ev)
    if dt is not None:
        import datetime as _dt

        window = _dt.timedelta(days=TEMPORAL_WINDOW_DAYS)
        near = sum(1 for other in tctx["sub_ts"] if abs(other - dt) <= window)
        if near >= TEMPORAL_MIN_NEIGHBORS:
            out.append("temporal_cluster")
    return out


def _tier(bound_elsewhere: bool, n_corr: int) -> str:
    """The frozen evidence-tier label — the precision dataset's key. The
    would-be-auto tier of the maintenance job (spec §0.5) is
    `name+2plus_nowhere`; measuring ITS accept rate on M's adjudication is
    the whole point of recording tiers."""
    if bound_elsewhere:
        return f"elsewhere+{min(n_corr, 2)}" + ("plus" if n_corr >= 2 else "")
    if n_corr == 0:
        return "name_only"
    if n_corr == 1:
        return "name+1_nowhere"
    return "name+2plus_nowhere"


# ---------------------------------------------------------------------------
# Candidate derivation — the gauge walk, re-used (spec §M.1)
# ---------------------------------------------------------------------------


def _derive(workspace_root, *, extra_thread_ids=(),
            now_iso: Optional[str] = None):
    """(report, internal) — the full propose computation. Read-only.

    `report` is JSON-serializable (the propose output and the labeled
    dataset's row shapes); `internal` maps seq -> (folded event, target tid,
    row) for apply's envelope composition."""
    root = Path(workspace_root)
    ent_doc = _read_json(root / "_hq" / "data" / "entities.json")
    entities = ent_doc if isinstance(ent_doc, dict) else {}
    ent = unwrap_entities(entities) if isinstance(entities, dict) else {}
    threads = [t for t in entities_collection(ent, "threads")
               if isinstance(t, dict) and t.get("id")]
    aliases_doc = _read_json(root / "_hq" / "data" / "aliases.json")

    # Owner-tier read + RECL1 fold FIRST — the gauge's exact stream
    # (binding_gauge.build_gauge does the same two steps in the same order).
    raw_events, _skipped = events_io.load_events_owner_scoped(root)
    folded = apply_reclassifications(raw_events)

    all_tids = {t["id"] for t in threads}
    by_tid = {t["id"]: t for t in threads}
    explicit = {str(t) for t in extra_thread_ids if str(t).strip()}
    unknown_explicit = sorted(explicit - all_tids)
    in_scope = {tid for tid in all_tids
                if (by_tid[tid].get("status") in SCOPE_STATUSES
                    or tid in explicit)}

    # GAUGECAL1 — pass the entities doc so the workspace-derived generic
    # terms are the SAME set the gauge measures with. Omitting it here would
    # re-open the §M.1 drift the shared helper exists to close, in its
    # nastiest direction: this module would propose bindings for rows whose
    # only evidence is the workspace's own brand name, and the gauge would
    # decline to count a single one of them.
    regex_by_tid = bg.terms_regexes(threads, aliases_doc, entities=entities)
    rejected = _rejected_pairs(folded)
    ctx = _thread_context(folded, all_tids, entities)
    org_linked = _org_thread_cache()
    email_idx = event_refs.email_person_index(entities)

    rows: list = []
    internal: dict = {}
    bound_count = {tid: 0 for tid in all_tids}
    name_unbound = {tid: 0 for tid in all_tids}
    refused = {"name_only": 0, "contested": 0, "no_seq": 0,
               "out_of_scope": 0, "already_rejected": 0}

    for ev in folded:
        if not isinstance(ev, dict) or not substance_events.is_substance_event(ev):
            continue
        refs = event_refs.threads_of(ev) & all_tids
        line = bg.event_match_line(ev)
        for tid in refs:
            bound_count[tid] += 1
        matches = bg.match_name_unbound(line, refs, regex_by_tid)
        for tid in matches:
            name_unbound[tid] += 1
        if not matches:
            continue
        if len(matches) >= 2:
            # Contested — matches 2+ threads' distinctive-term sets. Refused
            # silently (spec §0.5), counted whenever any in-scope thread is
            # involved (an out-of-scope-only contest is not this run's row).
            if any(tid in in_scope for tid in matches):
                refused["contested"] += 1
            continue
        (tid, term), = matches.items()
        if tid not in in_scope:
            refused["out_of_scope"] += 1
            continue
        seq = event_seq(ev)
        if seq is None:
            # No readable seq = no adjudication anchor and no
            # `supersedes_seq` for the fold. Refused, counted, honest.
            refused["no_seq"] += 1
            continue
        if (seq, tid) in rejected:
            refused["already_rejected"] += 1
            continue
        bound_elsewhere = bool(refs)
        corr = _corroborators(ev, tid, ctx, entities, root, org_linked,
                              email_idx)
        if not bound_elsewhere and not corr:
            # Spec §0.5: name-only with zero corroborators never enqueues.
            # (Bound-elsewhere rows always propose — they are the
            # misattribution story M specifically needs eyes on, and their
            # write is additive-only.)
            refused["name_only"] += 1
            continue
        dt = _event_dt(ev)
        cur_primary = _current_primary(ev)
        row = {
            "seq": seq,
            "thread_id": tid,
            "thread_name": str(by_tid[tid].get("canonical_name") or tid),
            "event_type": substance_events.event_type_of(ev) or "",
            "ts": dt.isoformat() if dt is not None else "",
            "preview": _preview(ev),
            "matched_term": term,
            "bound_elsewhere": bound_elsewhere,
            "current_primary": cur_primary,
            "current_refs": sorted(refs),
            "corroborators": corr,
            "n_corroborators": len(corr),
            "tier": _tier(bound_elsewhere, len(corr)),
            "auto_eligible_tier": (not bound_elsewhere and len(corr) >= 2),
        }
        rows.append(row)
        internal[seq] = (ev, tid, row)

    # Per-thread summary with k-to-READY (spec §M.2 — "so M sees what a
    # thread's confirmations buy"). Denominator is invariant under
    # re-binding; k = confirms needed to clear BOTH floors.
    # GAUGECAL1 — k-to-READY must count toward THIS workspace's bar, not the
    # operator audit's. Read once, out of the loop.
    min_substance, min_ratio = bg.ready_thresholds(root)
    per_thread: list = []
    for tid in sorted(in_scope):
        sub = bound_count[tid]
        den = sub + name_unbound[tid]
        n_rows = sum(1 for r in rows if r["thread_id"] == tid)
        if not n_rows and not name_unbound[tid]:
            continue
        k_sub = max(0, min_substance - sub)
        k_ratio = max(0, math.ceil(min_ratio * den - sub - 1e-9))
        k = max(k_sub, k_ratio)
        per_thread.append({
            "thread_id": tid,
            "thread_name": str(by_tid[tid].get("canonical_name") or tid),
            "status": by_tid[tid].get("status"),
            "substance_bound": sub,
            "name_unbound": name_unbound[tid],
            "proposable_rows": n_rows,
            "k_to_ready": k,
            "ready_reachable": bool(den) and k <= n_rows,
        })

    report = {
        "workspace_root": str(root),
        "generated_at": _now_iso(now_iso),
        "snapshot_max_seq": bg.max_human_seq(folded),
        "scope_statuses": sorted(SCOPE_STATUSES),
        "explicit_threads": sorted(explicit),
        "unknown_explicit_threads": unknown_explicit,
        "threads_in_scope": len(in_scope),
        "rows": rows,
        "n_rows": len(rows),
        "refused": refused,
        "per_thread": per_thread,
    }
    return report, internal


def propose(workspace_root, *, extra_thread_ids=(),
            now_iso: Optional[str] = None) -> dict:
    """READ-ONLY propose report (spec §M.2). Writes nothing — not
    events.jsonl, not the gauge artifact."""
    report, _internal = _derive(workspace_root,
                                extra_thread_ids=extra_thread_ids,
                                now_iso=now_iso)
    return report


# ---------------------------------------------------------------------------
# Envelope composition + the fences (spec §0.3 / §0.4)
# ---------------------------------------------------------------------------


def _corrected_envelope(folded_ev: dict, target_tid: str) -> dict:
    """The corrected envelope an accepted row's reclassification carries.

    EVERY current thread ref is lifted into the corrected envelope: the
    fold clears legacy data-level spellings on the patched copy, so any ref
    not lifted would be erased from the folded read (§0.4's opposite-
    direction denominator pollution).

      bound nowhere   -> primary = target, related = []
      bound elsewhere -> primary UNCHANGED, related = current refs + target
                         (minus the primary) — additive ONLY.
    """
    refs = set(event_refs.threads_of(folded_ev))
    old_primary = _current_primary(folded_ev)
    old_related = sorted(refs - ({old_primary} if old_primary else set()))
    if not refs:
        return {"old_primary": None, "old_related": [],
                "new_primary": target_tid, "new_related": []}
    new_related = sorted((refs | {target_tid})
                         - ({old_primary} if old_primary else set()))
    return {"old_primary": old_primary, "old_related": old_related,
            "new_primary": old_primary, "new_related": new_related}


def _validate_composed(composed: list) -> None:
    """The mutation-proofed no-primary-replacement fence (spec §V): a
    SEPARATE pass over the composed batch, so a bug (or a mutation) in
    `_corrected_envelope` cannot reach the substrate. A bound-elsewhere row
    whose composed write changes the primary, or any row whose target is
    missing from the corrected refs, refuses the WHOLE batch."""
    for c in composed:
        env, row = c["envelope"], c["row"]
        if row["bound_elsewhere"]:
            if env["new_primary"] != env["old_primary"]:
                raise BackfillGuardError(
                    f"REFUSED: composed write for seq {row['seq']} would "
                    f"replace a bound-elsewhere row's primary "
                    f"({env['old_primary']!r} -> {env['new_primary']!r}) — "
                    "bound-elsewhere is additive related_thread_ids ONLY "
                    "(SPEC_BACKFILL1 §0.4). Nothing was written.")
            if row["thread_id"] not in env["new_related"]:
                raise BackfillGuardError(
                    f"REFUSED: composed write for seq {row['seq']} does not "
                    f"add {row['thread_id']} to related_thread_ids. "
                    "Nothing was written.")
        else:
            if env["new_primary"] != row["thread_id"]:
                raise BackfillGuardError(
                    f"REFUSED: composed write for seq {row['seq']} binds "
                    f"primary to {env['new_primary']!r}, not the accepted "
                    f"target {row['thread_id']!r}. Nothing was written.")


# ---------------------------------------------------------------------------
# Apply — supervised, two-phase, one batch, one receipt (spec §M.3)
# ---------------------------------------------------------------------------


def apply(workspace_root, *, accept_seqs, reject_seqs=(),
          snapshot_max_seq: int, applied_by: str,
          source_skill: str = "backfill-bindings", extra_thread_ids=(),
          now_iso: Optional[str] = None) -> dict:
    """SUPERVISED WRITE. `accept_seqs` is the human's confirmation — a list
    of specific seqs from a propose report they read, or the literal string
    "all" (explicit, never the default). `reject_seqs` records the explicit
    "no" rows (the other half of the precision dataset). At least one of the
    two must be non-empty; with both empty NOTHING is written — no batch, no
    receipt (the no-auto fence's operational half).

    Refuses on snapshot drift (the substrate's human high-water mark moved
    past the propose report's `snapshot_max_seq` — re-propose). Re-derives
    fresh regardless and acts only on rows still applicable."""
    from event_gate import append_event

    root = Path(workspace_root)
    fresh, internal = _derive(root, extra_thread_ids=extra_thread_ids,
                              now_iso=now_iso)

    if snapshot_max_seq != fresh["snapshot_max_seq"]:
        return {
            "status": "stale_snapshot",
            "snapshot_max_seq": snapshot_max_seq,
            "current_max_seq": fresh["snapshot_max_seq"],
            "summary": ("REFUSED: the substrate moved past the propose "
                        f"snapshot (report at seq {snapshot_max_seq}, "
                        f"substrate at {fresh['snapshot_max_seq']}) — term "
                        "sets and candidate rows are only valid against the "
                        "snapshot they were derived from. Re-run propose "
                        "and adjudicate the fresh report. Nothing was "
                        "written."),
        }

    if accept_seqs == "all":
        accepts = [r["seq"] for r in fresh["rows"]]
    else:
        accepts = [int(s) for s in (accept_seqs or [])]
    rejects = [int(s) for s in (reject_seqs or [])]
    overlap = sorted(set(accepts) & set(rejects))
    if overlap:
        raise BackfillGuardError(
            f"REFUSED: seqs {overlap} appear in BOTH --accept and --reject. "
            "Nothing was written.")
    if not accepts and not rejects:
        return {"status": "no_op",
                "summary": "Nothing to adjudicate — no accepted or rejected "
                           "seqs given. Nothing was written."}

    # Phase 1 — compose.
    composed: list = []
    skipped: list = []
    for seq in accepts:
        entry = internal.get(seq)
        if entry is None:
            skipped.append({"seq": seq, "status": "skipped",
                            "detail": "not a currently applicable row — "
                                      "already bound, rejected earlier, or "
                                      "no longer name-matched-unbound"})
            continue
        folded_ev, tid, row = entry
        composed.append({"envelope": _corrected_envelope(folded_ev, tid),
                         "row": row})
    rejected_rows: list = []
    for seq in rejects:
        entry = internal.get(seq)
        if entry is None:
            skipped.append({"seq": seq, "status": "skipped",
                            "detail": "not a currently applicable row — "
                                      "nothing to reject"})
            continue
        _ev, tid, row = entry
        rejected_rows.append({"seq": seq, "thread_id": tid,
                              "tier": row["tier"],
                              "bind_basis": _bind_basis(row)})

    # Phase 2 — validate BEFORE any write (the whole batch or none).
    _validate_composed(composed)

    gauge_before = bg.build_gauge(root, now_iso=now_iso)
    ready_before = sum(1 for r in gauge_before["threads"].values()
                      if r["ready"])

    # Phase 3 — write: N reclassifications in ONE gated append, one batch.
    events_path = _events_path(root)
    batch_id = _mint_batch_id(now_iso)
    stamp = _batch_stamp(batch_id)
    to_append: list = []
    applied_rows: list = []
    for c in composed:
        env, row = c["envelope"], c["row"]
        data = {
            "origin": "backfilled",
            "bind_basis": _bind_basis(row),
            "target_thread_id": row["thread_id"],
            "evidence_tier": row["tier"],
            "matched_term": row["matched_term"],
            "supersedes_seq": row["seq"],
            "old_primary_thread_id": env["old_primary"],
            "new_primary_thread_id": env["new_primary"],
            "old_related_thread_ids": env["old_related"],
            "new_related_thread_ids": env["new_related"],
            "reason": "R3 binding backfill — human-confirmed re-bind "
                      f"(accepted by {applied_by})",
            "applied_by": applied_by,
        }
        data.update(stamp)
        to_append.append({
            "type": "reclassification",
            "source_skill": source_skill,
            "supersedes_seq": row["seq"],
            "primary_thread_id": env["new_primary"],
            "related_thread_ids": env["new_related"],
            # Human-confirmed — the same 1.0 the thread-split executor's
            # rail stamps on a confirmed move.
            "classification_confidence": 1.0,
            "data": data,
        })
        applied_rows.append({"seq": row["seq"], "thread_id": row["thread_id"],
                             "tier": row["tier"],
                             "bound_elsewhere": row["bound_elsewhere"]})
    if to_append:
        append_event(events_path, to_append, holder=source_skill)

    # READY after — computed on the post-write stream; the receipt below is
    # a system row and moves no verdict (substance_events classifies it
    # other_system), so stamping the receipt after this count is honest.
    if to_append:
        gauge_after = bg.build_gauge(root, now_iso=now_iso)
        ready_after = sum(1 for r in gauge_after["threads"].values()
                         if r["ready"])
    else:
        ready_after = ready_before

    receipt_data = {
        "batch_id": batch_id if to_append else None,
        "snapshot_max_seq": snapshot_max_seq,
        "rows_proposable": fresh["n_rows"],
        "applied": len(applied_rows),
        "applied_rows": applied_rows,
        "rejected": rejected_rows,
        "n_rejected": len(rejected_rows),
        "skipped": len(skipped),
        "refused_by_reason": fresh["refused"],
        "ready_before": ready_before,
        "ready_after": ready_after,
        "threads_total": len(gauge_before["threads"]),
        "confirmed_by": applied_by,
    }
    append_event(events_path, [{
        "type": RECEIPT_EVENT_TYPE,
        "source_skill": source_skill,
        "data": receipt_data,
    }], holder=source_skill)

    # Spec §0.7 — the flip is visible in the same gesture that caused it.
    # write_gauge is atomic; GAUGEJOB1's scheduled refresh stays the
    # steady-state cadence and last-writer-wins on identical inputs.
    gauge_doc = bg.write_gauge(root, now_iso=now_iso)

    n = len(applied_rows)
    noun = "event" if n == 1 else "events"
    summary = (f"Binding backfill — {n} {noun} re-bound, "
               f"{len(rejected_rows)} rejected (recorded), "
               f"READY {ready_before} -> {ready_after}.")
    if to_append:
        summary += f" Say `undo` (batch {batch_id}) to reverse the sitting."
    return {
        "status": "applied" if to_append else "recorded",
        "batch_id": batch_id if to_append else None,
        "batch_ref": ({"kind": "brain_batch", "batch_id": batch_id}
                      if to_append else None),
        "n_applied": n,
        "n_rejected": len(rejected_rows),
        "skipped": skipped,
        "receipt": receipt_data,
        "gauge_events_max_seq": gauge_doc.get("events_max_seq"),
        "summary": summary,
    }


def _bind_basis(row: dict) -> str:
    """The evidence string stamped into `data.bind_basis` (THREADBIND1's
    `thread_basis` idiom): which term matched + every corroborator."""
    parts = [f"name:{row['matched_term']}"] + list(row["corroborators"])
    if row["bound_elsewhere"]:
        parts.append("bound_elsewhere_additive")
    return "+".join(parts)


# ---------------------------------------------------------------------------
# Undo — CLI convenience over brain_undo (the registered reverser lives in
# brain_undo.REVERSERS["binding_backfill"]); ONE gauge rebuild after.
# ---------------------------------------------------------------------------


def undo(workspace_root, batch_id: str, *, undone_by: str,
         source_skill: str = "backfill-bindings") -> dict:
    """Reverse one `bkf_` batch via `brain_undo.undo_batch`, then rebuild
    the gauge ONCE (the reverser itself never rebuilds — a per-change
    rebuild would walk the whole substrate N times per sitting; a bare
    `undo` through another surface leaves the artifact honestly stale until
    the next refresh, and `events_max_seq` says so — reviewer-visible
    default)."""
    from brain_undo import undo_batch

    result = undo_batch(Path(workspace_root),
                        {"kind": "brain_batch", "batch_id": batch_id},
                        undone_by=undone_by, source_skill=source_skill)
    try:
        doc = bg.write_gauge(Path(workspace_root))
        result["gauge_ready_after_undo"] = sum(
            1 for r in doc["threads"].values() if r["ready"])
    except Exception as exc:  # never brick an undo on a gauge failure
        result["gauge_rebuild_error"] = f"{type(exc).__name__}: {exc}"
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_report(report: dict) -> None:
    print("Binding backfill — propose (writes nothing)")
    print(f"  snapshot_max_seq: {report['snapshot_max_seq']}  "
          f"(pass this to apply as --snapshot)")
    print(f"  scope: {'+'.join(report['scope_statuses'])} threads"
          + (f" + explicit {report['explicit_threads']}"
             if report["explicit_threads"] else ""))
    if report["unknown_explicit_threads"]:
        print(f"  WARN unknown --thread ids: "
              f"{report['unknown_explicit_threads']}")
    r = report["refused"]
    print(f"  rows: {report['n_rows']} proposable | refused: "
          f"{r['name_only']} name-only, {r['contested']} contested, "
          f"{r['no_seq']} no-seq, {r['out_of_scope']} out-of-scope, "
          f"{r['already_rejected']} already-rejected")
    for t in report["per_thread"]:
        reach = ("READY reachable" if t["ready_reachable"]
                 else "READY NOT reachable by binding alone")
        print(f"\n  {t['thread_name']} [{t['thread_id']}] "
              f"({t['status']}) — bound {t['substance_bound']}, "
              f"name-unbound {t['name_unbound']}, k-to-READY "
              f"{t['k_to_ready']} — {reach}")
        for row in report["rows"]:
            if row["thread_id"] != t["thread_id"]:
                continue
            flag = " [BOUND ELSEWHERE — additive only]" \
                if row["bound_elsewhere"] else ""
            corr = ",".join(row["corroborators"]) or "none"
            print(f"    seq {row['seq']}  {row['event_type']}  "
                  f"{row['ts'][:10]}  term={row['matched_term']!r}  "
                  f"corroborators={corr}  tier={row['tier']}{flag}")
            if row["preview"]:
                print(f"      | {row['preview']}")
            if row["current_refs"]:
                print(f"      | currently bound to: "
                      f"{', '.join(row['current_refs'])}")
    print("\n  adjudicate with: apply <ws> --snapshot "
          f"{report['snapshot_max_seq']} --accept <seqs|all> "
          "--reject <seqs> --applied-by <name>")


def main(argv=None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — non-console stream
            pass
    ap = argparse.ArgumentParser(
        description="SPEC_BACKFILL1 — R3 propose-confirm binding backfill "
                    "(NO auto-bind lane, by ruling).")
    sub = ap.add_subparsers(dest="command", required=True)

    p_prop = sub.add_parser("propose", help="read-only candidate report")
    p_prop.add_argument("workspace")
    p_prop.add_argument("--thread", action="append", default=[],
                        help="opt IN one out-of-scope thread id (repeatable)")
    p_prop.add_argument("--now", default=None, help="ISO now override (tests)")
    p_prop.add_argument("--json", action="store_true")

    p_apply = sub.add_parser(
        "apply", help="supervised adjudication of a propose report")
    p_apply.add_argument("workspace")
    p_apply.add_argument("--snapshot", type=int, required=True,
                         help="the snapshot_max_seq the propose report "
                              "printed — apply refuses if the substrate "
                              "moved past it")
    p_apply.add_argument("--accept", default="",
                         help="comma-separated confirmed seqs, or the "
                              "literal 'all' — explicit, never a default")
    p_apply.add_argument("--reject", default="",
                         help="comma-separated rejected seqs (recorded with "
                              "their evidence tier — the precision dataset)")
    p_apply.add_argument("--applied-by", required=True)
    p_apply.add_argument("--source-skill", default="backfill-bindings")
    p_apply.add_argument("--thread", action="append", default=[])
    p_apply.add_argument("--now", default=None)
    p_apply.add_argument("--json", action="store_true")

    p_undo = sub.add_parser("undo", help="reverse one bkf_ batch + one "
                                         "gauge rebuild")
    p_undo.add_argument("workspace")
    p_undo.add_argument("--batch", required=True)
    p_undo.add_argument("--undone-by", required=True)
    p_undo.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    root = Path(args.workspace)
    if not (root / "_hq" / "data").is_dir():
        print(f"ABORT: not a workspace root (no _hq/data): {root}",
              file=sys.stderr)
        return 2

    if args.command == "propose":
        report = propose(root, extra_thread_ids=args.thread, now_iso=args.now)
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            _print_report(report)
        return 0

    if args.command == "apply":
        accepts = "all" if args.accept.strip() == "all" else [
            s.strip() for s in args.accept.split(",") if s.strip()]
        rejects = [s.strip() for s in args.reject.split(",") if s.strip()]
        result = apply(root, accept_seqs=accepts, reject_seqs=rejects,
                       snapshot_max_seq=args.snapshot,
                       applied_by=args.applied_by,
                       source_skill=args.source_skill,
                       extra_thread_ids=args.thread, now_iso=args.now)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(result["summary"])
        return 0 if result["status"] in ("applied", "recorded") else 1

    if args.command == "undo":
        result = undo(root, args.batch, undone_by=args.undone_by)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, default=str))
        else:
            print(f"undo: {result.get('status')} — "
                  f"{result.get('n_undone')} restored, "
                  f"{result.get('n_errors')} errors; gauge READY now "
                  f"{result.get('gauge_ready_after_undo', '(rebuild failed)')}")
        return 0

    return 2


__all__ = [
    "BATCH_PREFIX",
    "BATCH_SALT_BYTES",
    "CHANGE_CLASS",
    "RECEIPT_EVENT_TYPE",
    "SCOPE_STATUSES",
    "TEMPORAL_WINDOW_DAYS",
    "TEMPORAL_MIN_NEIGHBORS",
    "BackfillGuardError",
    "propose",
    "apply",
    "undo",
    "_batch_stamp",
]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
