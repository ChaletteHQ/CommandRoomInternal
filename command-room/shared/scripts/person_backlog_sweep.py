#!/usr/bin/env python3
"""One-time person-proposal backlog sweep (T2.2 — FS-17 + FS-11b-extended).

WHY THIS EXISTS
The live staff-meeting queue carried ~68 legacy person proposals — the
identity backlog M has twice called "safe to batch-skip", plus a minority
with real context worth keeping. M's FS-11b ruling extended: auto-confirm far
more people (with observed emails), and sweep the aged low-context rest —
one narrated, undoable pass instead of 68 dropdown clicks.

WHAT IT DOES (per open person-family proposal, oldest first):

  RICH-CONTEXT (a name plus an inferred role and/or org)
      → `people_writer.auto_add_person` — the same-name dedup gate runs
        BEFORE every add (a token match → needs_confirm, left open and
        reported, never auto-forked); an email is captured ONLY when an
        address literally appears in the proposal's own evidence/source_ref
        text (an OBSERVED source, carried with provenance) — a pattern-guessed
        or constructed address is NEVER written (F-08 extends to capture).
        Then the person_added tombstone retires the proposal.

  LOW-CONTEXT + AGED (name-only mention, older than
  brain_proposals.PERSON_LOW_CONTEXT_STALE_DAYS)
      → expire: the not_relevant tombstone with an expiry note. Nothing else
        written.

  EVERYTHING ELSE (low-context but young; rich-context with a same-name
  collision) → LEFT OPEN, reported.

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
    carries the proposal row + the decision rationale."""
    from brain_proposals import (PERSON_LOW_CONTEXT_STALE_DAYS,
                                 person_proposal_is_low_context)
    from confirm_flow import load_open_person_proposals

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()

    # Review F-2 — honor the ACTIVE dismissal set exactly as the queue
    # adapter does: a proposal M snoozed ("snooze proposal 7d") must not be
    # auto-added or expired mid-snooze. Snoozed rows route to keep_open with
    # a visible rationale (better than silently skipping them from the plan).
    snoozed_seqs: set = set()
    try:
        import event_refs
        from mute_ledger import active_dismissal_target_ids

        events = event_refs.load_events(_events_path(ws)) \
            if _events_path(ws).exists() else []
        dismissed = active_dismissal_target_ids(events, now_iso)
        for tid in dismissed:
            t = str(tid)
            if t.startswith("person:"):
                t = t.split(":", 1)[1]
            if t.isdigit():
                snoozed_seqs.add(int(t))
    except Exception:
        snoozed_seqs = set()

    add: list[dict] = []
    expire: list[dict] = []
    keep: list[dict] = []
    for p in load_open_person_proposals(_events_path(ws)):
        name = (p.get("name") or "").strip()
        age = _age_days(p.get("captured_ts") or "", now_iso)
        low = person_proposal_is_low_context(p)
        if p.get("seq") in snoozed_seqs:
            keep.append({"proposal": p,
                         "why": "snoozed by M — the mute is honored; the "
                                "sweep never adjudicates a snoozed row"})
            continue
        if name and not low:
            email = _observed_email(p)
            add.append({"proposal": p, "email": email,
                        "why": "rich context — name + "
                               + ("role" if p.get("inferred_role") else "")
                               + ("+" if p.get("inferred_role") and p.get("inferred_org") else "")
                               + ("org" if p.get("inferred_org") else "")
                               + (f", observed address {email}" if email else ", no address in the source")})
        elif low and age is not None and age > PERSON_LOW_CONTEXT_STALE_DAYS:
            expire.append({"proposal": p,
                           "why": f"name-only mention, {age} days old "
                                  f"(window {PERSON_LOW_CONTEXT_STALE_DAYS}d)"})
        else:
            keep.append({"proposal": p,
                         "why": ("no name captured" if not name else
                                 f"name-only but only {age} days old — "
                                 "left for the TTL window")})
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
        # Tombstone the proposal + stamp the undo batch markers.
        tomb = build_person_proposal_resolved_event(
            p["seq"], resolution="person_added",
            source_skill="person-backlog-sweep",
            person_id=record["id"],
            note=f"backlog sweep {batch_id}")
        tomb.setdefault("data", {})
        tomb["data"]["brain_batch_id"] = batch_id
        tomb["data"]["brain_change_class"] = "person_org_creation_structured_fact"
        tomb["data"]["person_id"] = record["id"]
        append_event(events_path, [tomb], holder="person-backlog-sweep")
        results["added"].append({"seq": p.get("seq"), "name": p.get("name"),
                                 "person_id": record["id"],
                                 "email": entry["email"],
                                 "email_dropped_no_provenance":
                                     res.get("email_dropped_no_provenance")})
    for entry in plan["expire"]:
        p = entry["proposal"]
        try:
            tomb = build_person_proposal_resolved_event(
                p["seq"], resolution="not_relevant",
                source_skill="person-backlog-sweep",
                note=f"expired by backlog sweep {batch_id} — {entry['why']}")
            tomb.setdefault("data", {})
            tomb["data"]["brain_batch_id"] = batch_id
            tomb["data"]["brain_change_class"] = "person_proposal_tombstone"
            tomb["data"]["proposal_seq"] = p["seq"]
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
