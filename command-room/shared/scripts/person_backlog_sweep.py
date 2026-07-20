#!/usr/bin/env python3
"""One-time person-proposal backlog sweep (T2.2 — FS-17 + FS-11b-extended).

WHY THIS EXISTS
The live staff-meeting queue carried ~68 legacy person proposals — the
identity backlog M has twice called "safe to batch-skip", plus a minority
with real context worth keeping. M's FS-11b ruling extended: auto-confirm far
more people (with observed emails), and sweep the aged low-context rest —
one narrated, undoable pass instead of 68 dropdown clicks.

WHAT IT DOES (per open person-family proposal, oldest first). SPEC PID1
replaced this module's classifier: `plan_sweep` now DELEGATES to
`identity_reconcile.plan_reconcile` (one rule table, two entry shells) —
the add bar is the R1 corroboration doctrine, no longer "name +
prose-inferred role/org":

  AUTO-TIER CLUSTER (full name AND observed email / ≥2 independent source
  families AND zero same-name collision AND not already on file)
      → `people_writer.auto_add_person` — the same-name dedup gate runs
        BEFORE every add (a token match → needs_confirm, left open and
        reported, never auto-forked); an email is captured ONLY when an
        address literally appears in the cluster's own evidence/source_ref
        text (an OBSERVED source, carried with provenance) — a pattern-guessed
        or constructed address is NEVER written (F-08 extends to capture).
        Then a person_added tombstone retires EVERY member proposal.

  LOW-CONTEXT + AGED (name-only single mention, older than
  brain_proposals.PERSON_LOW_CONTEXT_STALE_DAYS)
      → expire: the not_relevant tombstone with an expiry note. Nothing else
        written.

  EVERYTHING ELSE (confirm clusters; on-file merge-propose clusters;
  no-name rows; update-type rows) → LEFT OPEN, reported with the tier
  named. The full link/annotation machinery is identity_reconcile's own
  entry point (`run_identity_reconcile` / `--backfill`).

UNDO — every write is reversible through the registered brain_undo reversers
(archive-never-delete): person adds reverse via person_org_creation_
structured_fact (status → archived), tombstones via person_proposal_tombstone
(person_proposal_reopened). The sweep stamps `brain_batch_id` on every write
marker so ONE `brain_undo.undo_batch({"kind": "brain_batch", "batch_id": ...})`
reverses the whole pass.

SAFETY
  - DRY-RUN IS THE DEFAULT. `--apply` performs the writes. The orchestrator
    session runs --apply against the live workspace only with M's go — this
    module never self-schedules and registers no job.
  - Narrated summary on stdout either way; a `person_backlog_swept` audit
    event records the batch (counts + batch id) on --apply.

stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _observed_email(p: dict) -> str | None:
    """An address is 'observed' ONLY if it literally appears in the
    proposal's own captured text (evidence / source_ref) — the message or
    meeting the person surfaced from. Never derived, never guessed.

    Review F-3 — quoted-thread evidence can carry the SENDER's or a third
    party's address, and "first regex hit" would attribute it to the wrong
    person. Accept an address only when:
      (a) it is the ONLY address in the captured text, or
      (b) its local part token-matches the person's name (quinn.alvarez@ →
          "Quinn Alvarez").
    Anything else → the person is added WITHOUT an email (attribution
    uncertainty is the no-email case, not a guess)."""
    text = f"{p.get('evidence') or ''} {p.get('source_ref') or ''}"
    hits = list(dict.fromkeys(m.group(0) for m in _EMAIL_RE.finditer(text)))
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    name_tokens = {t for t in re.split(r"[^a-z0-9]+", (p.get("name") or "").lower())
                   if len(t) >= 3}
    for h in hits:
        local_tokens = {t for t in re.split(r"[^a-z0-9]+", h.split("@", 1)[0].lower())
                        if t}
        if name_tokens & local_tokens:
            return h
    return None


def _age_days(ts: str, now_iso: str) -> int | None:
    from event_time import parse_ts

    a, b = parse_ts(ts), parse_ts(now_iso)
    if a is None or b is None:
        return None
    return (b - a).days


def plan_sweep(workspace_root, *, now_iso: str | None = None) -> dict:
    """Pure planning pass (no writes): classify every open person proposal.
    Returns {add: [...], expire: [...], keep_open: [...]} where each entry
    carries the proposal row + the decision rationale.

    SPEC PID1 D7 — classification DELEGATES to `identity_reconcile.
    plan_reconcile` (one rule table, two entry shells; a forked classifier
    is the FS-19 pre-history all over again). The bar therefore changed
    from the old "name + prose-inferred role/org" to the R1 corroboration
    doctrine: only AUTO-tier clusters (full name AND observed email / ≥2
    source families AND zero collision AND not on file) plan as adds.
    Confirm clusters, on-file (merge-propose) clusters, no-name rows, and
    update-type rows all route to keep_open with the tier named — the full
    link/annotation machinery is `identity_reconcile`'s own entry point
    (`run_identity_reconcile` / `--backfill`); this shell keeps its
    original narrow add+expire shape for back-compat."""
    from identity_reconcile import plan_reconcile

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    rp = plan_reconcile(ws, now_iso=now_iso)

    def _rep_row(cluster: dict) -> dict:
        # Oldest add row, carrying the cluster's BEST name spelling — the
        # row auto_add_person / narration reads.
        rows = sorted(cluster["add_rows"],
                      key=lambda r: r.get("captured_ts") or "") \
            if cluster.get("add_rows") else list(cluster.get("rows") or [])
        rep = dict(rows[0]) if rows else {}
        rep["name"] = cluster.get("name")
        return rep

    add: list[dict] = []
    expire: list[dict] = []
    keep: list[dict] = []
    for entry in rp["auto"]:
        add.append({"proposal": _rep_row(entry["cluster"]),
                    "cluster": entry["cluster"],
                    "email": entry["email"],
                    "why": entry["why"]})
    for entry in rp["expire"]:
        expire.append({"proposal": entry["proposal"], "why": entry["why"]})
    for entry in rp["confirm"]:
        keep.append({"proposal": _rep_row(entry["cluster"]),
                     "why": entry["why"]})
    for entry in rp["merge_propose"]:
        keep.append({"proposal": _rep_row(entry["cluster"]),
                     "why": entry["why"]})
    for entry in rp["annotations"]:
        keep.append({"proposal": entry["proposal"],
                     "why": "no name captured — the identity reconciler "
                            "converts these to annotations (D5)"})
    for entry in rp["keep_open"]:
        row = entry.get("proposal") or (_rep_row(entry["cluster"])
                                        if entry.get("cluster") else {})
        keep.append({"proposal": row, "why": entry["why"]})
    for row in rp.get("updates") or []:
        keep.append({"proposal": row,
                     "why": "update to an existing record — adjudicated in "
                            "the queue, never batch-applied"})
    return {"add": add, "expire": expire, "keep_open": keep,
            "now_iso": now_iso}


def run_sweep(workspace_root, *, apply: bool = False,
              now_iso: str | None = None) -> dict:
    """Plan and (with apply=True) execute. Returns the plan plus per-item
    results and the undo batch id."""
    from event_gate import append_event

    ws = Path(workspace_root)
    plan = plan_sweep(ws, now_iso=now_iso)
    batch_id = "pbs_" + _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = {"added": [], "needs_confirm": [], "expired": [], "errors": []}
    if not apply:
        plan["batch_id"] = batch_id
        plan["applied"] = False
        return plan

    from confirm_flow import build_person_proposal_resolved_event
    from people_writer import auto_add_person

    events_path = _events_path(ws)
    for entry in plan["add"]:
        p = entry["proposal"]
        try:
            res = auto_add_person(
                ws,
                canonical_name=p["name"].strip(),
                email=entry["email"],
                email_provenance=({"via": "person_proposal",
                                   "proposal_seq": p.get("seq"),
                                   "source_ref": p.get("source_ref")}
                                  if entry["email"] else None),
                source_skill="person-backlog-sweep",
                role=p.get("inferred_role") or None,
            )
        except Exception as exc:  # loud per-item, contained per-batch
            results["errors"].append({"seq": p.get("seq"),
                                      "error": f"{type(exc).__name__}: {exc}"})
            continue
        if res.get("status") == "needs_confirm":
            # Same-name collision — NEVER auto-forked. Left open for the
            # widget/disambiguation path.
            results["needs_confirm"].append({"seq": p.get("seq"),
                                             "name": p.get("name")})
            continue
        record = res["record"]
        # Tombstone EVERY member proposal in the identity cluster (PID1 D3:
        # the duplicate mentions are the corroboration evidence — the
        # cluster consumes them, they all close on the one add) + stamp the
        # undo batch markers. Seq-less members tombstone via the D8
        # fingerprint.
        members = (entry.get("cluster") or {}).get("rows") or [p]
        tombs = []
        for m in members:
            seq = m.get("seq")
            kwargs = {}
            if not isinstance(seq, int) or isinstance(seq, bool):
                seq = None
                kwargs["proposal_fingerprint"] = m.get("fingerprint")
            tomb = build_person_proposal_resolved_event(
                seq, resolution="person_added",
                source_skill="person-backlog-sweep",
                person_id=record["id"],
                note=f"backlog sweep {batch_id}", **kwargs)
            tomb.setdefault("data", {})
            tomb["data"]["brain_batch_id"] = batch_id
            tomb["data"]["brain_change_class"] = "person_org_creation_structured_fact"
            tomb["data"]["person_id"] = record["id"]
            tombs.append(tomb)
        append_event(events_path, tombs, holder="person-backlog-sweep")
        results["added"].append({"seq": p.get("seq"), "name": p.get("name"),
                                 "person_id": record["id"],
                                 "email": entry["email"],
                                 "n_proposals": len(members),
                                 "email_dropped_no_provenance":
                                     res.get("email_dropped_no_provenance")})
    for entry in plan["expire"]:
        p = entry["proposal"]
        try:
            seq = p.get("seq")
            kwargs = {}
            if not isinstance(seq, int) or isinstance(seq, bool):
                seq = None  # D8 — seq-less rows tombstone by fingerprint
                kwargs["proposal_fingerprint"] = p.get("fingerprint")
            tomb = build_person_proposal_resolved_event(
                seq, resolution="not_relevant",
                source_skill="person-backlog-sweep",
                note=f"expired by backlog sweep {batch_id} — {entry['why']}",
                **kwargs)
            tomb.setdefault("data", {})
            tomb["data"]["brain_batch_id"] = batch_id
            tomb["data"]["brain_change_class"] = "person_proposal_tombstone"
            append_event(events_path, [tomb], holder="person-backlog-sweep")
            results["expired"].append({"seq": p.get("seq"),
                                       "name": p.get("name")})
        except Exception as exc:
            results["errors"].append({"seq": p.get("seq"),
                                      "error": f"{type(exc).__name__}: {exc}"})
    # ONE audit event for the whole pass.
    append_event(events_path, [{
        "type": "person_backlog_swept",
        "source_skill": "person-backlog-sweep",
        "data": {
            "batch_id": batch_id,
            "n_added": len(results["added"]),
            "n_expired": len(results["expired"]),
            "n_needs_confirm": len(results["needs_confirm"]),
            "n_kept_open": len(plan["keep_open"]),
            "n_errors": len(results["errors"]),
        },
    }], holder="person-backlog-sweep")
    plan["batch_id"] = batch_id
    plan["applied"] = True
    plan["results"] = results
    return plan


def _narrate(plan: dict) -> str:
    lines = []
    mode = "APPLIED" if plan.get("applied") else "DRY RUN — nothing written"
    lines.append(f"Person backlog sweep ({mode})")
    lines.append(f"  add as contacts: {len(plan['add'])}")
    for e in plan["add"]:
        p = e["proposal"]
        lines.append(f"    + {p.get('name')!r} — {e['why']}")
    lines.append(f"  expire (aged, name-only): {len(plan['expire'])}")
    for e in plan["expire"]:
        lines.append(f"    - {e['proposal'].get('name') or '(no name)'!r} — {e['why']}")
    lines.append(f"  left open: {len(plan['keep_open'])}")
    for e in plan["keep_open"]:
        lines.append(f"    = {e['proposal'].get('name') or '(no name)'!r} — {e['why']}")
    if plan.get("applied"):
        res = plan["results"]
        lines.append(
            f"  applied: {len(res['added'])} added, {len(res['expired'])} "
            f"expired, {len(res['needs_confirm'])} held for a same-name "
            f"confirm, {len(res['errors'])} errors")
        lines.append(f"  undo: the whole pass reverses with batch id "
                     f"{plan['batch_id']} (adds archive, expiries reopen)")
    return "\n".join(lines)


def main() -> int:
    # Review F-5: Windows pipes default to cp1252 — the output carries
    # non-ASCII (middots, warning glyphs) and would crash the CLI.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="perform the writes (default: dry-run plan only)")
    ap.add_argument("--now", default=None, help="ISO now override (tests)")
    ap.add_argument("--json", action="store_true",
                    help="emit the machine-readable plan as well")
    args = ap.parse_args()
    plan = run_sweep(args.workspace, apply=args.apply, now_iso=args.now)
    print(_narrate(plan))
    if args.json:
        print(json.dumps(plan, default=str))
    return 0


__all__ = ["plan_sweep", "run_sweep"]


if __name__ == "__main__":
    raise SystemExit(main())
