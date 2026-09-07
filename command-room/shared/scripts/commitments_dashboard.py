#!/usr/bin/env python3
"""
commitments_dashboard.py — payload builder for the "My Open Commitments"
sidebar artifact (artifact id `my-commitments`, installed by
level-up-command-room, Mode: My Open Commitments).

SKILLMERGE1 D10 (2026-09-03): ported from the operator's per-workspace script
into the plugin, per the ruling that anything useful to every client belongs
in the plugin, not in a per-client script folder. The port keeps the
original's one load-bearing contract and adopts three of the plugin's:

  * REDUCER OWNS TRUTH — AND SINCE PLATE1 NIGHT 2 (2026-09-04) THE REDUCER IS
    THE PLATE. This module never re-derives "what is open", "how many", or
    what a row wants next: it calls `plate_view.build_plate` (the ONE
    grouping — DD-1) and keeps the rows whose bucket is you-owe, so this page
    and `what's on my plate` place the same row in the same block for the
    same reason, on the same numbers (`count_commitments`, carried on the
    plate's own view). The page's own overdue/quiet/stuck derivation is GONE
    — it was a second grouping of the same rows wearing different words, and
    one of those words ("stuck") is on the customer-banned list the plate's
    renderer enforces (NUMBERS1 DD-3 / PLATE1 P4).
  * THE PRIMARY USER IS RESOLVED, NEVER GUESSED (S5). The owner filter runs
    on `primary_user.resolve_primary_user`. When it returns None the build
    REFUSES with one plain line — there is no `person_001` fallback, because
    a wrong guess renders somebody else's promises under the user's name.
  * THE TIMEZONE COMES FROM tz.py (S5). `today` is the WORKSPACE-LOCAL date
    (a UTC date tips boundary items — exactly 21 quiet days — into stuck and
    disagrees with the headline). No hardcoded zone: `tz.load_workspace_tz`
    is the one reader; when it cannot resolve, the build degrades to UTC and
    SAYS SO in the payload (`tz_unresolved`), per tz.py's contract.
  * OWNER SURFACE, READ-ONLY. The artifact renders the owner's own open set
    in the owner's own sidebar and composes nothing outward; every button on
    it sends a chat phrase. Internal identifiers (commitment / person /
    project ids, source skill) never reach the HTML — `render_input` keeps
    display fields only.

Scope rendered: OPEN + CONFIRMED + owned by the workspace user ("you owe").
Unconfirmed extractions (`data.pending_review`) are excluded from the rows and
reported only as the pointer count into the needs-your-call queue (which is
what `counts["headline"]["unconfirmed"]` already is).

Usage (from the plugin root, as the skill's bash step):

  python3 shared/scripts/commitments_dashboard.py --workspace-root <WS> \
      --output /tmp/cr-mc-input.json            # render_artifact.py input
  python3 shared/scripts/render_artifact.py \
      --template shared/templates/commitments_dashboard.html \
      --input /tmp/cr-mc-input.json --output /tmp/cr-my-commitments.html

Exit codes: 0 built; 3 refused (primary user unresolved — the one line is on
stdout AND stderr so the skill can relay it verbatim); 2 usage / I/O error.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

ARTIFACT_ID = "my-commitments"
TEMPLATE_REL = "shared/templates/commitments_dashboard.html"
SOURCE_SKILL = "level-up-command-room"

# The one refusal. Plain words, no ids, no file names — it is relayed to the
# user verbatim by the skill.
REFUSED_LINE = (
    "I can't build My Open Commitments yet: I don't know which person in "
    "this workspace is you, so I can't tell your promises from everyone "
    "else's. Say `set up command room` to fix that, then ask again."
)

# Display fields that reach the artifact. Everything else in a row is
# substrate handle (ids, source skill) and stays in the payload only.
# PLATE1 night 2: `stuck` (the page's own flag, and a banned customer word)
# is replaced by the plate's own `block` / `block_label` / `reason` — what
# the row wants next, in the plate's words.
DISPLAY_FIELDS = (
    "title", "kind", "cp_name", "bucket", "thread_name", "age_days", "due",
    "overdue", "quiet_days", "block", "block_label", "reason",
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _as_map(records) -> dict:
    """id -> record for a list-shaped collection (the canonical shape);
    dict-shaped input passes through. Non-dict records are dropped."""
    if isinstance(records, dict):
        return {k: v for k, v in records.items() if isinstance(v, dict)}
    if isinstance(records, list):
        return {r.get("id"): r for r in records
                if isinstance(r, dict) and r.get("id")}
    return {}


def _parse_date(v) -> Optional[_dt.date]:
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return _dt.date.fromisoformat(v.strip()[:10])
    except ValueError:
        return None


def _local_now(workspace_root, now: Optional[_dt.datetime]) -> tuple[_dt.datetime, bool]:
    """(workspace-local now, tz_unresolved). No hardcoded zone anywhere."""
    from tz import TZResolutionError, load_workspace_tz
    base = now or _dt.datetime.now(_dt.timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=_dt.timezone.utc)
    try:
        zone = load_workspace_tz(workspace_root)
    except TZResolutionError:
        return base.astimezone(_dt.timezone.utc), True
    return base.astimezone(zone), False


def _entities(workspace_root) -> dict:
    p = Path(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------
# the builder
# --------------------------------------------------------------------------

def build_payload(workspace_root, *, now: Optional[_dt.datetime] = None) -> dict:
    """Build the dashboard payload for one workspace.

    Returns {"status": "ok", ...payload} or {"status": "refused", "line":
    REFUSED_LINE} when the primary user cannot be resolved. Pure read.
    """
    from commitment_state import BUCKET_YOU_OWE, count_commitments
    from cru_match import load_open_commitments, split_pending_review
    from entities_io import entities_collection
    from plate_view import BLOCK_DO_IT, BLOCK_TITLES, build_plate
    from primary_user import resolve_primary_user

    ws = Path(workspace_root)
    events_path = ws / "_hq" / "data" / "events.jsonl"

    user_id = resolve_primary_user(ws)
    if not user_id:
        return {"status": "refused", "line": REFUSED_LINE, "artifact": ARTIFACT_ID}

    local_now, tz_unresolved = _local_now(ws, now)
    today = local_now.date()

    ent = _entities(ws)
    people = _as_map(entities_collection(ent, "people")) if ent else {}
    orgs = _as_map(entities_collection(ent, "orgs")) if ent else {}
    projects = _as_map(entities_collection(ent, "threads")) if ent else {}

    def pname(pid):
        p = people.get(pid) or {}
        return p.get("canonical_name") or p.get("name") or p.get("display_name") or ""

    def porg(pid):
        p = people.get(pid) or {}
        return p.get("org_id") or p.get("org") or p.get("organization_id") or ""

    def oname(oid):
        o = orgs.get(oid) or {}
        return o.get("canonical_name") or o.get("name") or ""

    def projname(tid):
        t = projects.get(tid) or {}
        return t.get("canonical_name") or t.get("name") or ""

    def projorg(tid):
        t = projects.get(tid) or {}
        return t.get("org_id") or t.get("org") or t.get("affiliation_id") or ""

    # ---- THE PLATE IS THE GROUPING (PLATE1 DD-1, adopted night 2) -------
    # One call: the same model, the same blocks, the same reasons, the same
    # counts as `what's on my plate`. The rows this page renders are the
    # plate's you-owe rows; everything the page used to derive for itself
    # (overdue, quiet, "stuck") rides the row already.
    plate = build_plate(ws, user_person_id=user_id,
                        now_iso=local_now.isoformat(),
                        # F-4 — take the fold it already did rather than
                        # re-reading the stream for our own copy.
                        include_movement=True)
    if not plate.get("ok"):
        return {"status": "refused", "line": plate.get("error") or REFUSED_LINE,
                "artifact": ARTIFACT_ID}
    plate_rows = {r["id"]: r for r in plate["rows"]}
    # ONE full-history pass fewer (REVIEW_PLATE1_N2 F-4). The movement map
    # used to be re-derived here — a second read and fold of the whole stream
    # — for `quiet_days` on each row AND for the headline's movement figures.
    # Both now come from the plate: the row carries `movement_days`, and
    # `build_plate` hands back the fold itself, which this page passes to the
    # counter so its headline still equals
    # `commitment_state.commitment_counts(workspace_root)` key for key
    # (`stuck` / `blocked` exist only when the counter is given a map).
    # The counting call itself stays — that parity IS this page's contract,
    # and the plate keeps only the headline half of the counter's dict.
    movement = plate.get("movement")
    opens = load_open_commitments(events_path, workspace_root=str(ws))
    counts = count_commitments(opens, user_person_id=user_id,
                               now_iso=today.isoformat(), movement=movement)

    # INTAKE seam: the rows are the CONFIRMED half only. The counter above
    # saw the whole projection (it needs the pending rows to point at the
    # needs-your-call queue); the list never does.
    confirmed, _needs_review = split_pending_review(opens)
    rows = []
    for ev in confirmed:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if (d.get("owner_id") or "") != user_id:
            continue
        cid = d.get("id") or ""
        title = (d.get("title") or d.get("summary") or "").strip()
        summary = (d.get("summary") or "").strip()
        cp = d.get("counterparty_id") or ""
        if not cp:
            for pid in ev.get("person_ids") or []:
                if pid != user_id:
                    cp = pid
                    break
        tid = ev.get("primary_thread_id") or d.get("thread_id") or ""
        org_id = porg(cp) or projorg(tid) or ""
        created = _parse_date(ev.get("ts"))
        age = (today - created).days if created else None
        due = _parse_date(d.get("effective_due") or d.get("due") or "")
        overdue = bool(due and due < today)
        # `quiet_days` rides the plate row (F-4 — no second movement fold).
        last_move = (plate_rows.get(cid) or {}).get("movement_days")
        prow = plate_rows.get(cid)
        if not prow or prow.get("bucket") != BUCKET_YOU_OWE:
            # THE PLATE DECIDES. A row it does not carry at all is not a row
            # here either — a sub-item (it rides its parent's line, SUB1), a
            # cluster sibling folded into a survivor (CLUSTER1), an
            # observed-tier line (never a row, D5) — and a row it carries in
            # someone else's lane is not yours. This page never re-derives
            # ownership, membership, or what a row wants.
            continue
        rows.append({
            "id": cid,
            "title": title or "(untitled commitment)",
            "summary": summary if summary != title else "",
            "kind": d.get("kind") or "promise",
            "cp_id": cp,
            "cp_name": pname(cp),
            "org_id": org_id,
            "org_name": oname(org_id),
            "thread_id": tid,
            "thread_name": projname(tid),
            "created": created.isoformat() if created else "",
            "age_days": age,
            "due": due.isoformat() if due else "",
            "overdue": overdue,
            "quiet_days": last_move,
            # THE PLATE'S OWN ANSWER for this row — what it wants next, and
            # why, in the words `render_plate` composed (never re-said here).
            "block": prow.get("block") or "",
            "block_label": BLOCK_TITLES.get(prow.get("block") or "", ""),
            "reason": prow.get("reason") or "",
            "source": ev.get("source_skill") or "",
            "bucket": oname(org_id) or projname(tid) or "Unassigned",
        })
    rows.sort(key=lambda r: (not r["overdue"], r["block"] != BLOCK_DO_IT,
                             -(r["age_days"] or 0)))

    built_local = local_now.strftime("%b %d, %Y %I:%M %p")
    if tz_unresolved:
        built_local += " UTC"
    return {
        "status": "ok",
        "artifact": ARTIFACT_ID,
        "built_at": local_now.isoformat(),
        "built_local": built_local,
        "tz_unresolved": tz_unresolved,
        "user_id": user_id,
        "user_name": pname(user_id) or "You",
        "counts": counts,
        "rows": rows,
    }


def render_input(payload: dict) -> dict:
    """The render_artifact.py input: placeholder -> pre-formatted STRING.
    Display fields only — no ids, no source skill, no substrate handles."""
    if payload.get("status") != "ok":
        raise ValueError("render_input needs a built payload, not a refusal")
    rows = [{k: r.get(k) for k in DISPLAY_FIELDS} for r in payload["rows"]]
    counts = payload.get("counts") or {}
    data = {
        "counts": {"total": counts.get("total"), "headline": counts.get("headline", {})},
        "rows": rows,
        "built": payload["built_local"],
        "user": payload["user_name"],
    }
    return {
        "DATA_JSON": json.dumps(data, ensure_ascii=False),
        "BUILT": payload["built_local"],
        "USER": payload["user_name"],
    }


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Build the My Open Commitments render input.")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None,
                        help="render_artifact.py input JSON (default: stdout)")
    parser.add_argument("--now", type=str, default=None,
                        help="Override 'now' for deterministic testing (ISO 8601).")
    args = parser.parse_args(argv)

    now = None
    if args.now:
        try:
            now = _dt.datetime.fromisoformat(args.now)
        except ValueError:
            print(f"ERROR: --now is not ISO 8601: {args.now!r}", file=sys.stderr)
            return 2
    if not (args.workspace_root / "_hq" / "data" / "entities.json").exists():
        print(f"ERROR: no _hq/data/entities.json under {args.workspace_root}", file=sys.stderr)
        return 2

    payload = build_payload(args.workspace_root, now=now)
    if payload.get("status") != "ok":
        print(payload["line"])
        print(payload["line"], file=sys.stderr)
        return 3
    values = render_input(payload)
    text = json.dumps(values, ensure_ascii=False, indent=1)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(json.dumps({"rows": len(payload["rows"]),
                          "headline": (payload.get("counts") or {}).get("headline", {}),
                          "tz_unresolved": payload["tz_unresolved"]}))
    else:
        sys.stdout.write(text)
    return 0


__all__ = ["ARTIFACT_ID", "TEMPLATE_REL", "SOURCE_SKILL", "REFUSED_LINE",
           "DISPLAY_FIELDS", "build_payload", "render_input"]


if __name__ == "__main__":
    sys.exit(main())
