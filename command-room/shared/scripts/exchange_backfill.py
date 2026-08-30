#!/usr/bin/env python3
"""
exchange_backfill — SPEC EXCHBACK1: the standing pile gets the exchange window.

WHY THIS EXISTS. EXCH1 (2026-08-24) fixed `meeting_capture`'s floor
classifier for NEW captures — it now reads the reply, not just the trigger
utterance (`accepted_in_exchange`, `superseded_in_meeting`'s recommit leg).
Every row captured BEFORE that fix was adjudicated by the old blind
classifier and still sits in the held/unconfirmed pile carrying a reason that
may be factually false: "never accepted" for an offer the very next turn
took up, "taken back" for a scope narrowed but still committed.

WHAT THIS MODULE IS
====================
  load_pile           — PURE READ. The held/unconfirmed rows EXCH1 governs:
                         open, `pending_review`, floor-gated commitments
                         whose `review_reason` resolves to
                         `FLOOR_CODE_NOT_ACCEPTED` or
                         `FLOOR_CODE_SUPERSEDED_IN_MEETING` — the two codes
                         `accepted_in_exchange` / `superseded_in_meeting` can
                         answer differently than the pre-EXCH1 classifier did.
  readjudicate         — PURE. Re-runs `meeting_capture.admit_meeting_capture`
                         — THE SAME function a live capture calls, imported,
                         never re-derived — over one row's stored evidence
                         against a supplied transcript window. Zero logic
                         duplication: every verdict is the live path's own.
  propose_backfill     — READ-ONLY. Walks a batch of the pile, resolves each
                         row's evidence window, re-adjudicates, and reports
                         only the DELTAS (old reason -> new reason old code
                         actually changed to a NEW code) — an unchanged
                         verdict is never proposed. Writes nothing: not
                         `events.jsonl`, not any other store.
  apply_backfill       — SUPERVISED WRITE, EXISTING WRITERS ONLY. Re-derives
                         the delta set fresh (never trusts a stale propose
                         report), then applies every CONFIRMED, currently
                         "release"-actionable delta through
                         `commitment_state.clear_review_flags` — the row's
                         floor cleared, so it releases from the review pile
                         exactly the way a human's own Keep-both tap does.
                         Every write in ONE freshly minted `exb_` brain
                         batch (mirrors CLUSTER1's `clu_` batch, same shape,
                         own prefix), so one `brain_undo.undo_batch` reverses
                         the whole run. ONE receipt event
                         (`exchange_backfill_run`) closes the run — the same
                         receipt discipline `reconcile_forward` uses.

THE DOCTRINE (SPEC EXCHBACK1 §0, binding)
==========================================
1. Supervised, never silent. `propose_backfill` proposes; nothing changes
   state without an explicit confirmed id (or `"all"`) reaching
   `apply_backfill`. `batch_cap` bounds a single walk so a big pile is
   worked in digestible chunks, never all at once.
2. Canonical writers only. The one write path here is
   `commitment_state.clear_review_flags` — no new event type for the
   release, no relaxed floor. A delta whose new verdict is STILL floor-gated
   (the reason changed but the item did not clear) is reported
   (`action: "report_only"`) and never applied: releasing it would be
   wrong (it is still below the floor), and there is no sanctioned writer
   that rewrites an append-only capture event's `review_reason` in place.
   That is a deliberate scope line, not an oversight — see the build
   record's Open Questions.
3. Read-only re-adjudication first, verdict deltas only. A row whose new
   floor code equals its old floor code is UNTOUCHED and NOT proposed.
4. Honest absence. A row whose evidence window cannot be resolved (no
   `source_ref`, or nothing at the local transcript cache path) is reported
   in `unadjudicable`, never guessed at.

THE TRANSCRIPT CACHE. This module introduces the one convention it needs:
`<workspace_root>/_hq/data/transcripts/<slug(source_ref)>.txt`, read-only,
plain UTF-8 text. Nothing else in this codebase persists a meeting
transcript locally (captures store only clipped evidence excerpts), so a
production caller re-fetching from the live connector passes its own
`transcript_lookup` callable — `default_transcript_lookup` is the fallback
that makes "the cache has nothing" the honest, testable default rather than
a silent guess.

Stdlib only except the plugin's own shared/scripts imports.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Callable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from meeting_capture import (  # noqa: E402
    FLOOR_CODE_NOT_ACCEPTED,
    FLOOR_CODE_SUPERSEDED_IN_MEETING,
    accepted_in_exchange,
    admit_meeting_capture,
    floor_reason_code,
    superseded_in_meeting,
)

# The undo contract: one gesture = one `exb_` batch; `brain_undo.undo_batch`
# reverses every stamped write in it. Same shape as CLUSTER1's `clu_`, own
# prefix.
BATCH_PREFIX = "exb_"
BATCH_SALT_BYTES = 4

# `clear_review_flags` stamped with the SAME class CLUSTER1's survivor-confirm
# uses (`commitment_confirm`) — the registered `brain_undo` reverser for it
# (`_reverse_commitment_confirm` -> `needs_review_queue.undo_confirm_items`)
# restores the row's ORIGINAL `pending_review` + `review_reason`, read off
# the untouched capture event. No new reverser, no new write shape.
RELEASE_CHANGE_CLASS = "commitment_confirm"

# The one receipt event this module writes, and only on `apply` (a `propose`
# run writes nothing at all — Acceptance: events.jsonl byte-identical).
RECEIPT_EVENT_TYPE = "exchange_backfill_run"

# The two floor codes EXCH1's exchange-window layer can answer differently
# than the pre-fix classifier: NOT_ACCEPTED (accepted_in_exchange may clear
# it) and SUPERSEDED_IN_MEETING (the `final` re-commit check may clear a
# retraction the pre-EXCH1 supersede scan would have gated).
EXCH1_GOVERNED_CODES = frozenset(
    {FLOOR_CODE_NOT_ACCEPTED, FLOOR_CODE_SUPERSEDED_IN_MEETING}
)

# Ruling §0-1 — a big pile is walked in digestible chunks, never all at once.
DEFAULT_BATCH_CAP = 25


# -----------------------------------------------------------------------------
# Paths + the transcript cache
# -----------------------------------------------------------------------------


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _transcripts_dir(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "transcripts"


def _slug(source_ref) -> str:
    """Filesystem-safe key for a meeting reference — the transcript cache's
    own filename, not the meeting-reference key `meeting_capture.
    meeting_ref_keys` indexes by (a different job: membership, not a path)."""
    s = str(source_ref or "").strip().lower()
    slug = re.sub(r"[^a-z0-9_.-]+", "_", s).strip("_")
    return slug or "unref"


def default_transcript_lookup(workspace_root) -> Callable[[str], Optional[str]]:
    """The local transcript-cache reader: `(source_ref) -> text | None`.

    Read-only. Returns None (never raises) for anything missing or
    unreadable — the caller folds that into `unadjudicable`, honest absence
    rather than a guess (ruling §0-4)."""
    root = Path(workspace_root)

    def _lookup(source_ref: str) -> Optional[str]:
        p = _transcripts_dir(root) / f"{_slug(source_ref)}.txt"
        if not p.exists() or not p.is_file():
            return None
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            return None
        return text if text.strip() else None

    return _lookup


# -----------------------------------------------------------------------------
# Batch id — same mint as CLUSTER1's `clu_`, own prefix.
# -----------------------------------------------------------------------------


def _mint_batch_id(now_iso=None) -> str:
    """`exb_<UTC-to-second>-<8 hex>`. One human-confirmed gesture = one
    batch = one `undo`."""
    import secrets

    from event_time import parse_ts

    now = (parse_ts(str(now_iso)) if now_iso else None) \
        or _dt.datetime.now(_dt.timezone.utc)
    stamp = now.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{BATCH_PREFIX}{stamp}-{secrets.token_hex(BATCH_SALT_BYTES)}"


def _now_iso(now_iso=None) -> str:
    from event_time import parse_ts

    now = (parse_ts(str(now_iso)) if now_iso else None) \
        or _dt.datetime.now(_dt.timezone.utc)
    return now.isoformat()


def _batch_stamp(batch_id: str) -> dict:
    """The `brain_batch_id` / `brain_change_class` kwargs a release write
    carries — factored out so the mutation suite can drop the stamp BY NAME
    (`exchange_backfill._batch_stamp = lambda batch_id: {}`) and watch the
    undo round-trip pin go red (nothing on disk would carry the batch id, so
    `brain_undo.resolve_batch` finds zero changes to reverse)."""
    return {"brain_batch_id": batch_id, "brain_change_class": RELEASE_CHANGE_CLASS}


def _commitment_id(row: dict) -> str:
    d = row.get("data") if isinstance(row.get("data"), dict) else {}
    return d.get("id") or row.get("id") or f"commitment_seq_{row.get('seq', '?')}"


# -----------------------------------------------------------------------------
# The pile
# -----------------------------------------------------------------------------


def load_pile(workspace_root) -> list:
    """The held/unconfirmed rows EXCH1 governs (SPEC §The work, bullet 1).

    `needs_review_queue` / `cru_match.load_needs_review` is the one
    definition of the needs-your-call queue; this filters that projection
    down to the floor-gated rows whose `review_reason` resolves to a code
    `accepted_in_exchange` / `superseded_in_meeting` can answer — a fusion
    refusal or a relevance-tier row is out of scope (EXCH1 never touches
    those verdicts, so re-running the floor on them would never differ).

    Read-only. Append order preserved (oldest first), same as the queue."""
    from cru_match import load_needs_review

    events_path = _events_path(workspace_root)
    rows = load_needs_review(str(events_path), workspace_root=str(workspace_root))
    out = []
    for row in rows:
        if row.get("type") != "commitment":
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        if not data.get("floor_gated"):
            continue
        code = floor_reason_code(data.get("review_reason"))
        if code in EXCH1_GOVERNED_CODES:
            out.append(row)
    return out


def _deciding_quote(data: dict, transcript_text, old_code: str,
                    new_code: str, new_reason: str) -> str:
    """The reply text that DECIDED the delta — called directly off the two
    EXCH1 functions themselves (both imported, both used here; the ONLY
    duplication-free way to report the excerpt a verdict doesn't otherwise
    carry a lane for).

    A rescue off NOT_ACCEPTED is decided by `accepted_in_exchange`'s own
    quote — `admit_meeting_capture` clears the floor but has no lane to
    carry that quote forward once the item proceeds to fusion/relevance, so
    this is the one place backfill calls the acceptance check itself, for
    the excerpt, never for the verdict (the verdict is `readjudicate`'s
    `admit_meeting_capture` call, always). A verdict that resolves to
    SUPERSEDED_IN_MEETING is decided by `superseded_in_meeting`'s own quote,
    the same way."""
    if old_code == FLOOR_CODE_NOT_ACCEPTED and not new_reason:
        return accepted_in_exchange(data, transcript_text)["quote"]
    if new_code == FLOOR_CODE_SUPERSEDED_IN_MEETING:
        return superseded_in_meeting(data, transcript_text)["quote"]
    return ""


def _is_delta(result: dict) -> bool:
    """Ruling §0-3 — THE delta filter. An UNCHANGED verdict (the new floor
    code equals the old one) is never proposed. Named and called by
    `propose_backfill` rather than inlined so the mutation suite can unwire
    it BY NAME (`exchange_backfill._is_delta = lambda r: True`) and watch
    the unchanged-absent pin go red."""
    return not result["unchanged"]


def readjudicate(row: dict, transcript_text) -> dict:
    """Pure. Re-runs `meeting_capture.admit_meeting_capture` — THE live
    capture path, imported, never re-derived — over one pile row's stored
    evidence against the supplied transcript window.

    Returns {"commitment_id", "title", "source_ref", "old_reason",
    "old_code", "new_reason", "new_code", "fusion_reason",
    "deciding_reply_excerpt", "unchanged"}. `unchanged` compares CODES, not
    prose — a receipt-worded rephrase of the same verdict is not a delta.

    `fusion_reason` (reviewer fix, REVIEW_EXCHBACK1 F1) is the live
    verdict's own fusion refusal, carried so the release gate can read the
    WHOLE verdict: `accepted_in_exchange` anchors evidence-then-TITLE while
    the fusion check refuses on the evidence field alone, so a rescue can
    clear the floor on a row the live path still holds in review as a
    fusion refusal — and releasing that row would relax a gate §0-2 says
    this module must never relax."""
    data = dict(row.get("data") or {})
    old_reason = str(data.get("review_reason") or "")
    old_code = floor_reason_code(old_reason)
    verdict = admit_meeting_capture(data, transcript_text=transcript_text)
    new_reason = str(verdict.get("floor_reason") or "")
    new_code = floor_reason_code(new_reason)
    quote = _deciding_quote(data, transcript_text, old_code, new_code, new_reason)
    return {
        "commitment_id": _commitment_id(row),
        "title": str(data.get("title") or ""),
        "source_ref": str(data.get("source_ref") or ""),
        "old_reason": old_reason,
        "old_code": old_code,
        "new_reason": new_reason,
        "new_code": new_code,
        "fusion_reason": str(verdict.get("fusion_reason") or ""),
        "deciding_reply_excerpt": quote,
        "unchanged": new_code == old_code,
    }


# -----------------------------------------------------------------------------
# Propose — read-only.
# -----------------------------------------------------------------------------


def propose_backfill(
    workspace_root,
    *,
    now_iso: Optional[str] = None,
    transcript_lookup: Optional[Callable[[str], Optional[str]]] = None,
    batch_cap: Optional[int] = DEFAULT_BATCH_CAP,
    offset: int = 0,
) -> dict:
    """READ-ONLY. Walks up to `batch_cap` pile rows starting at `offset`,
    resolves each row's evidence window, re-adjudicates, and reports the
    delta set. Writes NOTHING — not `events.jsonl`, not any other store
    (Acceptance: byte-identical before/after).

    `batch_cap=None` walks the whole pile in one pass (used internally by
    `apply_backfill`, which must never act on a stale sub-window)."""
    pile = load_pile(workspace_root)
    total_pile = len(pile)
    window = pile[offset:offset + batch_cap] if batch_cap else pile[offset:]
    lookup = transcript_lookup or default_transcript_lookup(workspace_root)

    deltas: list = []
    unadjudicable: list = []
    n_unchanged = 0

    for row in window:
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        source_ref = data.get("source_ref")
        transcript_text = None
        if source_ref:
            try:
                transcript_text = lookup(source_ref)
            except Exception:
                transcript_text = None
        if not source_ref or not transcript_text:
            unadjudicable.append({
                "commitment_id": _commitment_id(row),
                "title": str(data.get("title") or ""),
                "source_ref": str(source_ref or ""),
                "reason": ("row carries no source_ref to resolve" if not
                          source_ref else
                          "evidence window not found — the source transcript "
                          "is no longer resolvable"),
            })
            continue

        result = readjudicate(row, transcript_text)
        # Ruling §0-3 — an UNCHANGED verdict is untouched and never proposed.
        if not _is_delta(result):
            n_unchanged += 1
            continue
        # Reviewer fix (REVIEW_EXCHBACK1 F1): release only when the LIVE
        # verdict would actually leave review. A rescued floor with a
        # standing fusion refusal is still a held row — report it, never
        # release it.
        still_held = bool(result["new_code"]) or bool(result["fusion_reason"])
        result["action"] = "release" if not still_held else "report_only"
        deltas.append(result)

    walked = len(window)
    remaining = max(0, total_pile - offset - walked)
    return {
        "workspace_root": str(workspace_root),
        "generated_at": _now_iso(now_iso),
        "rows_in_pile": total_pile,
        "rows_walked": walked,
        "offset": offset,
        "batch_cap": batch_cap,
        "remaining_after_this_batch": remaining,
        "deltas": deltas,
        "n_deltas": len(deltas),
        "n_applicable": sum(1 for d in deltas if d["action"] == "release"),
        "unchanged": n_unchanged,
        "unadjudicable": unadjudicable,
        "n_unadjudicable": len(unadjudicable),
    }


# -----------------------------------------------------------------------------
# Apply — supervised, canonical writers only.
# -----------------------------------------------------------------------------


def apply_backfill(
    workspace_root,
    commitment_ids,
    *,
    applied_by: str,
    source_skill: str,
    now_iso: Optional[str] = None,
    transcript_lookup: Optional[Callable[[str], Optional[str]]] = None,
) -> dict:
    """SUPERVISED WRITE (SPEC §0-1/§0-2) — EXISTING WRITERS ONLY.

    `commitment_ids`: the human's CONFIRMATION — a list of the specific
    commitment ids to release (from a `propose_backfill` report the caller
    already showed a person), or the literal string `"all"` to apply every
    currently release-actionable delta.

    Re-derives the delta set FRESH (a whole-pile `propose_backfill`, never a
    stale cached report) so a row resolved between propose and apply is
    never double-acted-on. Only `action == "release"` deltas are ever
    applied — a `report_only` delta (still floor-gated under a different
    reason) is skipped by construction; there is no sanctioned writer for
    it (ruling §0-2).

    Every applied row's write goes through
    `commitment_state.clear_review_flags` — the SAME writer a human's own
    Keep-both tap uses — stamped into ONE freshly minted `exb_` brain batch,
    so `brain_undo.undo_batch(batch_ref)` reverses the whole run. ONE
    receipt event (`exchange_backfill_run`) closes the run — rows walked,
    deltas found, applied, unadjudicable — the same receipt discipline
    `reconcile_forward` uses.

    Returns {"status", "batch_id", "batch_ref", "n_applied", "n_skipped",
    "results", "receipt", "summary"}."""
    fresh = propose_backfill(
        workspace_root, now_iso=now_iso, transcript_lookup=transcript_lookup,
        batch_cap=None,
    )
    releasable = {d["commitment_id"]: d for d in fresh["deltas"]
                 if d["action"] == "release"}

    if commitment_ids == "all":
        targets = list(releasable.keys())
    else:
        targets = [str(c) for c in (commitment_ids or [])]

    from commitment_state import CommitmentIdError, clear_review_flags

    batch_id = _mint_batch_id(now_iso)
    results: list = []
    n_applied = 0
    n_skipped = 0

    for cid in targets:
        delta = releasable.get(cid)
        if delta is None:
            results.append({
                "commitment_id": cid, "status": "skipped",
                "detail": "not a currently applicable delta — already "
                          "resolved, no longer in the pile, or "
                          "report_only (still floor-gated under a "
                          "different reason)",
            })
            n_skipped += 1
            continue
        excerpt = delta["deciding_reply_excerpt"]
        note = (f"exchange-window backfill: {delta['old_reason']!r} cleared"
               f" — {excerpt}" if excerpt
               else f"exchange-window backfill: {delta['old_reason']!r} cleared")
        try:
            res = clear_review_flags(
                workspace_root, cid, cleared_by=applied_by,
                source_skill=source_skill, note=note,
                source_ref=(delta.get("source_ref") or None),
                **_batch_stamp(batch_id),
            )
        except CommitmentIdError as exc:
            results.append({"commitment_id": cid, "status": "error",
                            "detail": str(exc)})
            n_skipped += 1
            continue
        status = res.get("status")
        if status == "cleared":
            n_applied += 1
        else:
            n_skipped += 1
        results.append({
            "commitment_id": cid, "status": status,
            "old_reason": delta["old_reason"], "new_reason": delta["new_reason"],
        })

    receipt_data = {
        "batch_id": batch_id,
        "rows_in_pile": fresh["rows_in_pile"],
        "rows_walked": fresh["rows_walked"],
        "deltas_found": fresh["n_deltas"],
        "applied": n_applied,
        "skipped": n_skipped,
        "unadjudicable": fresh["n_unadjudicable"],
        "confirmed_by": applied_by,
    }
    from event_gate import append_event
    receipt_ev = {
        "type": RECEIPT_EVENT_TYPE,
        "source_skill": source_skill,
        "data": receipt_data,
    }
    append_event(str(_events_path(workspace_root)), receipt_ev,
                holder=source_skill)

    if n_applied:
        status = "applied"
    elif not targets:
        status = "no_op"
    else:
        status = "error"
    noun = "row" if n_applied == 1 else "rows"
    summary = (f"Exchange-window backfill — {n_applied} {noun} released "
              f"from review. Say `undo` to put them back.")
    if n_skipped:
        summary += f" {n_skipped} skipped — details per row."

    return {
        "status": status,
        "batch_id": batch_id,
        "batch_ref": {"kind": "brain_batch", "batch_id": batch_id},
        "n_applied": n_applied,
        "n_skipped": n_skipped,
        "results": results,
        "receipt": receipt_data,
        "summary": summary,
    }


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def _print_propose_report(report: dict) -> None:
    print("Exchange-window backfill — propose (writes nothing)")
    print(f"  pile: {report['rows_in_pile']} rows  walked: {report['rows_walked']}"
         f" (offset {report['offset']}, cap {report['batch_cap']})")
    print(f"  deltas: {report['n_deltas']} ({report['n_applicable']} applicable)"
         f"  unchanged: {report['unchanged']}"
         f"  unadjudicable: {report['n_unadjudicable']}")
    for d in report["deltas"]:
        new = d["new_reason"] or "(floor cleared)"
        print(f"  [{d['action']}] {d['commitment_id']}  {d['old_reason']!r} -> {new!r}")
        if d["deciding_reply_excerpt"]:
            print(f"      -> {d['deciding_reply_excerpt']}")
    for u in report["unadjudicable"]:
        print(f"  [unadjudicable] {u['commitment_id']}  {u['reason']}")
    if report["remaining_after_this_batch"]:
        nxt = report["offset"] + (report["batch_cap"] or 0)
        print(f"  {report['remaining_after_this_batch']} more rows remain in "
             f"the pile — run again with --offset {nxt}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="SPEC EXCHBACK1 — supervised re-adjudication backfill "
                    "for the exchange-window fix.")
    sub = ap.add_subparsers(dest="command", required=True)

    p_propose = sub.add_parser("propose", help="read-only delta report")
    p_propose.add_argument("workspace")
    p_propose.add_argument("--now", default=None, help="ISO now override (tests)")
    p_propose.add_argument("--batch-cap", type=int, default=DEFAULT_BATCH_CAP)
    p_propose.add_argument("--offset", type=int, default=0)
    p_propose.add_argument("--json", action="store_true")

    p_apply = sub.add_parser("apply", help="supervised apply of confirmed deltas")
    p_apply.add_argument("workspace")
    p_apply.add_argument("--ids", required=True,
                         help="comma-separated confirmed commitment ids, or "
                              "the literal 'all' (explicit — reviewer fix "
                              "REVIEW_EXCHBACK1 F3: the least-supervised "
                              "path must never be the default)")
    p_apply.add_argument("--applied-by", required=True)
    p_apply.add_argument("--source-skill", default="exchange-backfill")
    p_apply.add_argument("--now", default=None)
    p_apply.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.command == "propose":
        report = propose_backfill(args.workspace, now_iso=args.now,
                                  batch_cap=args.batch_cap, offset=args.offset)
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            _print_propose_report(report)
        return 0

    if args.command == "apply":
        ids = "all" if args.ids == "all" else [
            s.strip() for s in args.ids.split(",") if s.strip()
        ]
        result = apply_backfill(args.workspace, ids, applied_by=args.applied_by,
                                source_skill=args.source_skill, now_iso=args.now)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(result["summary"])
        return 0

    return 2


__all__ = [
    "BATCH_PREFIX",
    "BATCH_SALT_BYTES",
    "RELEASE_CHANGE_CLASS",
    "RECEIPT_EVENT_TYPE",
    "EXCH1_GOVERNED_CODES",
    "DEFAULT_BATCH_CAP",
    "default_transcript_lookup",
    "load_pile",
    "readjudicate",
    "propose_backfill",
    "apply_backfill",
    "_is_delta",
    "_batch_stamp",
]


if __name__ == "__main__":
    raise SystemExit(main())
