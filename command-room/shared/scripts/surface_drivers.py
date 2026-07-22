#!/usr/bin/env python3
"""One-command surface drivers (T2.2 scope 1e — kill the ~30-command prep).

WHY THIS EXISTS
The RV round measured a manual `commitments` fire spending ~30 shell/python
round-trips assembling the data view before the widget rendered (load →
project → count → escalate → sort → annotate → build → fit → persist), with
the double-render nit (RV-3) riding on the re-runs. Every one of those steps
is deterministic — so this module does ALL of them in ONE CLI invocation per
surface and prints exactly what the runtime needs to relay:

    python3 shared/scripts/surface_drivers.py commitments \
        --workspace <WORKSPACE> [--page N] [--page-size 15]
    python3 shared/scripts/surface_drivers.py staff-meeting \
        --workspace <WORKSPACE> [--page N] [--moves-json <file>] \
        [--fired-via scheduled|manual|catchup]
    python3 shared/scripts/surface_drivers.py waiting-on \
        --workspace <WORKSPACE> [--page N] [--chase-json <file>] \
        [--fired-via scheduled|manual|catchup]

STDOUT SHAPE (fixed contract — the skill texts pin it):

    CR-PAGINATION: {"page": 1, "total_pages": 3, ...}
    CR-WIDGET-HTML-BEGIN
    <the persisted page's validated bytes, verbatim>
    CR-WIDGET-HTML-END
    CR-RECEIPT: {"task_id": ..., "status": "written"}   (only with --fired-via)

FIRE RECEIPTS (FB-7): `--fired-via <run mode>` makes the page-1 invocation
ALSO append the surface's canonical per-fire receipt (receipts.log_receipt)
inside this same call — the 2026-07-16 live staff-meeting scheduled fire
rendered its widget but never reached the prose receipt step that came after
the widget post, so the render and the receipt are now one invocation that
no orchestrator path can split. Pages 2+ (`show more`) never receipt.

The runtime relays the bytes BETWEEN the BEGIN/END markers to
`mcp__visualize__show_widget` as `widget_code`, byte-exact, and reads the
pagination line for the position/`show more` narration. Nothing else needs
running: `render_and_persist` (validators + byte-fit + persist + audit file)
already ran inside this call.

IDEMPOTENT-SINGLE-CALL (RV-3 double-render): one driver invocation per page
per fire. The persisted audit file is written once per invocation; re-running
the driver "to refresh" writes a second audit file and is exactly the
double-render defect. If the transport output for the requested page is
already in hand, relay it — never re-run.

Views are built from the CANONICAL projectors only (cru_match /
commitment_state / confirm_flow / brain_proposals) — the driver adds no
judgment, no filtering, no re-derivation. Read-only against the substrate
except for the transport's own persist into `_hq/.system/widgets/`.

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

# The commitment-triage row verb sets (SKILL.md Step 3 — verbatim).
_PROMISE_VERBS = ["resolved", "push to [date]", "drop", "not mine",
                  "make task", "never track this", "skip"]
_TASK_VERBS = ["resolved", "push to [date]", "drop", "promote",
               "never track this", "skip"]
# FB-20 — how many money-class items the brief names in prose before it
# leaves the rest to the pointer's count. A render bound, never a silence:
# every capped item is still inside `queue_pointer["count"]`, and money is
# the only class that gets named at all.
MONEY_PROSE_CAP = 3


def _pointer_line(count: int) -> str:
    """FB-20's ONE queue-pointer line — the brief's entire adjudication
    affordance now that the card is gone. Drop-empty at zero: a brief with
    nothing queued says nothing about the queue (never "0 things need your
    eyes", never an all-clear pad)."""
    if count <= 0:
        return ""
    noun = "thing needs" if count == 1 else "things need"
    return f"{count} {noun} your eyes — say `staff meeting`."


# Unconfirmed-block confirm cluster (W4b).
_CONFIRM_VERBS = ["mine", "theirs to [name]", "make task", "drop"]
_DUP_VERBS = ["merge", "keep both", "drop"]
# pending_review rows outside the 7d escalation pin (explicit confirm-shaped
# actions only — an explicit click IS confirmation).
_PENDING_VERBS = ["resolved", "drop", "not mine"]
# SUB1 D6 — child rows get the standard per-kind dropdown MINUS the one verb
# that doesn't apply to a child: `never track this` (suppression rules key on
# capture shape — children aren't captures) stays parent-level. Everything
# else — Done, Later…, Drop, Not mine, Make task / Promote, Skip — works on
# a child with zero special-casing (children are real commitments).
# (`add to my list` was the other parent-level carve-out until MLK1 retired
# the verb entirely — no row emits it now.)
_CHILD_PROMISE_VERBS = ["resolved", "push to [date]", "drop", "not mine",
                        "make task", "skip"]
_CHILD_TASK_VERBS = ["resolved", "push to [date]", "drop", "promote", "skip"]

_REDUCED_REASON = ("Fewer options — the owner is unconfirmed; clicking Done, "
                   "Drop, or Not mine confirms it.")

# FB-15 — the daily Waiting On chat (CTS1 Surface 1; orchestrator-commitments)
# row verb sets, post-FB-17. Delegated tasks (owner != user, effective kind
# `task`) are CRU-INELIGIBLE, so they get NO PRE-STAGED chase draft — but the
# orchestrator set DOES carry `draft`, which composes the nudge ON DEMAND at
# dispatch (orchestrator-commitments §417 / §958 / §965). The verb id is
# connector-free in the deterministic row: this driver still never composes a
# body or touches a connector at render time — apply-choices routes the click
# through email-writer's lazy-draft path only when the user taps it. A fully
# resolved, pre-staged chase still rides chase_rows like any other email row.
# `draft` LEADS the manual set, matching the spec order.
#
# The pending_review / unowned confirm tail asks an OWNERSHIP question, so it
# carries the ownership cluster (orchestrator-commitments §452 + § "Confirm
# section actions" §1027-1033), not the opaque person-record `confirm` verb:
#   - `mine`             — CLAIMS (commitment_state.confirm_commitment_owner:
#                          sets the owner AND clears any pending_review flag)
#   - `theirs to [name]` — ROUTES (reassign_commitment, confirmed=True)
#   - `drop`             — closes an item that is nobody's (§452 dismissal)
#   - `not relevant`     — the soft 60-day mute
#   - `add to my plate`  — CTS1FIX D5: pull it onto My Plate as an owned task
# `make task` is deliberately omitted: reclassifying a commitment to a task in
# place while its owner is still unknown is incoherent — `add to my plate`
# (make it MY task) is the coherent task path here. Dispatch is keyed per
# row-id, so the ONE shared cluster preserves the genuinely-different behavior
# between the two classes (mine clears the pending_review flag only where one
# is present; on a bare-unowned row it simply stamps the owner).
_DELEGATED_VERBS = ["draft", "mark received", "snooze 3d", "add to my plate"]
_REVIEW_VERBS = ["mine", "theirs to [name]", "drop", "not relevant",
                 "add to my plate"]


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _age_days(ts: str, now_iso: str) -> int | None:
    from event_time import parse_ts

    a, b = parse_ts(ts), parse_ts(now_iso)
    if a is None or b is None:
        return None
    return max(0, int((b - a).total_seconds() // 86400))


def _due_phrase(due, now_iso: str) -> str:
    if not due:
        return "undated"
    try:
        d = _dt.date.fromisoformat(str(due)[:10])
        today = _dt.date.fromisoformat(now_iso[:10])
        if d == today:
            return "due today"
        if d < today:
            return f"overdue since {d.strftime('%b')} {d.day}"
        return f"due {d.strftime('%b')} {d.day}"
    except ValueError:
        return f"due {due}"


def _people_by_id(ws: Path) -> dict:
    """id -> person record from entities.json.

    Defensive: a missing / corrupt entities.json yields an empty map (the
    caller then falls back to owner_external / a bare 'delegated' tag and the
    `add email then send` recovery verb)."""
    try:
        raw = (ws / "_hq" / "data" / "entities.json").read_text("utf-8")
        people = (json.loads(raw) or {}).get("people") or []
    except Exception:
        return {}
    out: dict = {}
    for p in people:
        pid = p.get("id")
        if pid:
            out[pid] = p
    return out


# RRF1 — the one review_reason clause class whose staleness is mechanically
# checkable at render time: capture_gate stamps it when the counterparty had
# no person record AT CAPTURE and never revisits it.
_RR_NO_PERSON_RE = re.compile(r"^counterparty '(.+)' has no person record$")


def _display_review_reason(ws: Path, raw, cache: dict) -> str:
    """Render-time overlay for the frozen "counterparty 'X' has no person
    record" review_reason clause (RRF1 + UXC1 plain-language ruling
    2026-07-21). The STORED clause is never shown raw — "counterparty" is
    banned vocabulary (VOICE_CALIBRATION glossary): an unresolved name
    renders as "'X' isn't in your contacts yet"; if X NOW resolves to a
    person record (entity_resolve ladder — the same one brain_proposals
    uses; A6: one home for the matcher) it renders as "'X' — contact added
    ✓" so the row stops telling the CEO to do something they already did.
    Every other clause class passes through verbatim.

    DISPLAY-ONLY: the STORED review_reason is a gating input (cru_match /
    commitment_dedup / confirm_flow / identity_reconcile read it) and is
    never written back — this rewrites the rendered string only.

    `cache` is a per-driver-call memo (name -> bool): each distinct name
    costs at most one resolve_all call per render pass (resolve_all re-reads
    entities.json internally, so the memo IS the read fence), and reasons
    with no eligible clause never load the resolver at all.
    """
    raw = str(raw)
    if "has no person record" not in raw:
        return raw
    out = []
    for clause in raw.split("; "):
        m = _RR_NO_PERSON_RE.match(clause)
        if not m:
            out.append(clause)
            continue
        name = m.group(1)
        if name not in cache:
            try:
                from entity_resolve import resolve_all
                cache[name] = any(r.entity_type == "person"
                                  for r in resolve_all(ws, name))
            except Exception:
                # Display nicety only — a resolver failure (fresh workspace,
                # mid-sync entities.json) must never break a surface render.
                cache[name] = False
        out.append(f"'{name}' — contact added ✓" if cache[name]
                   else f"'{name}' isn't in your contacts yet")
    return "; ".join(out)


def _owner_display_name(ev: dict, people_by_id: dict) -> str | None:
    """Resolve the delegated-task owner's display name for the row tag.

    A delegated row is owner != M by definition, so it should name who: read a
    display name carried on the event first (legacy `owner_display` /
    `owner_name`), else resolve `owner_id` (multi-shape via `_commitment_field`)
    through entities.json, else fall back to the raw `owner_external` string.
    Returns None when nothing resolves — the caller then renders bare
    'delegated'."""
    from cru_match import _commitment_field

    d = ev.get("data") or {}
    for k in ("owner_display", "owner_name"):
        v = (d.get(k) or ev.get(k) or "")
        if isinstance(v, str) and v.strip():
            return v.strip()
    owner_id = _commitment_field(ev, "owner_id")
    rec = people_by_id.get(owner_id) if owner_id else None
    if rec:
        name = (rec.get("name") or rec.get("canonical_name") or "").strip()
        if name:
            return name
    ext = d.get("owner_external") or ev.get("owner_external") or ""
    if isinstance(ext, str) and ext.strip():
        return ext.strip()
    return None


def _owner_email(ev: dict, people_by_id: dict) -> str | None:
    """The delegated owner's first actionable email, or None.

    `draft` is a send-class verb (chat_output_renderer Gate 6 / Bug #44): a row
    that exposes it MUST carry a valid To: address or the renderer refuses to
    ship it. Resolve `owner_id` -> the person record's canonical `emails` array
    (via `people_writer.get_person_emails`); if `owner_external` is itself an
    email, use that. None -> the caller degrades `draft` to the canonical
    `add email then send` recovery verb (no To: required)."""
    from cru_match import _commitment_field
    from people_writer import get_person_emails

    owner_id = _commitment_field(ev, "owner_id")
    rec = people_by_id.get(owner_id) if owner_id else None
    if rec:
        emails = get_person_emails(rec)
        if emails:
            return emails[0]
    ext = ((ev.get("data") or {}).get("owner_external")
           or ev.get("owner_external") or "")
    if isinstance(ext, str) and "@" in ext and "." in ext.split("@")[-1]:
        return ext.strip()
    return None


def build_commitment_triage_view(workspace_root, *, now_iso: str | None = None) -> dict:
    """The full commitment-triage data view (SKILL.md Steps 1-2, mechanized):
    canonical loader + bucket export + escalation split + age sections +
    per-row context tags + per-kind verb sets. Pure read."""
    from commitment_activity import derive_commitment_movement
    from commitment_state import (commitment_kind, count_commitments,
                                  stale_tasks)
    from confirm_flow import select_unconfirmed_escalation, unconfirmed_classes
    from cru_match import load_open_commitments
    from event_time import event_time
    from primary_user import resolve_primary_user

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    events_path = _events_path(ws)
    opens = load_open_commitments(events_path)
    try:
        user_id = resolve_primary_user(ws)
    except Exception:
        user_id = None
    movement = derive_commitment_movement(events_path)
    counts = count_commitments(opens, user_person_id=user_id,
                               now_iso=now_iso, movement=movement)
    stale_ids = {row.get("commitment_id") or row.get("id")
                 for row in stale_tasks(opens, now_iso, movement=movement)}
    esc = select_unconfirmed_escalation(opens, now_iso)
    esc_ids = {row["commitment_id"] for row in esc["pin"]}
    propose_drop_ids = {row["commitment_id"] for row in esc["propose_drop"]}

    def _cid(ev) -> str:
        d = ev.get("data") or {}
        return d.get("id") or f"commitment_seq_{ev.get('seq')}"

    # SUB1 D6 — the family nests: sub-items render INSIDE their parent's row
    # (the existing sub_items shape), never as their own top-level rows.
    # Pagination is family-atomic structurally: paginate_data_view slices
    # top-level items only, so a family that doesn't fit moves whole to the
    # next page (a 12-child family alone on an oversized page degrades via
    # the transport's over_budget flag rather than splitting). Orphan
    # children (parent closed — the cascade crash window) partition
    # top-level and render as ordinary rows with a "was part of" note.
    from cru_match import partition_subitems
    top_level, sub_level = partition_subitems(opens)
    subs_by_parent: dict = {}
    for ev in sub_level:
        subs_by_parent.setdefault(
            (ev.get("data") or {}).get("parent_id"), []).append(ev)

    display_n = 0
    sections: list[dict] = []
    rr_cache: dict = {}  # RRF1 per-render-pass memo (name -> resolves now)

    # Unconfirmed block FIRST (v4.6.1 W4b escalation — never age-buried).
    if esc["pin"]:
        rows = []
        for r in esc["pin"]:
            display_n += 1
            dup = bool(r.get("suspected_duplicate_of"))
            lead = ""
            if r["commitment_id"] in propose_drop_ids:
                lead = (f"sat unconfirmed for {r['days_unconfirmed']} days — "
                        f"drop it? · ")
            tag = (f"{lead}captured {r['days_unconfirmed']} days ago — still "
                   f"unconfirmed"
                   + (f" · {_display_review_reason(ws, r['review_reason'], rr_cache)}"
                      if r.get("review_reason") else ""))
            rows.append({
                "n": r["commitment_id"], "display_n": display_n,
                "name": r.get("title") or "(untitled)",
                "context_tag": tag,
                "actions": _DUP_VERBS if dup else _CONFIRM_VERBS,
            })
        sections.append({"title": "Unconfirmed", "count": len(rows),
                         "items": rows})

    # Age sections, oldest first; escalation-pinned rows excluded (no
    # double-surfacing). SUB1: top-level items only — children render nested.
    aged: list[tuple[int, dict]] = []
    for ev in top_level:
        cid = _cid(ev)
        if cid in esc_ids:
            continue
        age = _age_days(ev.get("ts") or "", now_iso)
        aged.append((age if age is not None else -1, ev))
    aged.sort(key=lambda t: -t[0])

    def _row(ev, age):
        nonlocal display_n
        display_n += 1
        cid = _cid(ev)
        d = ev.get("data") or {}
        kind = commitment_kind(ev)
        classes = unconfirmed_classes(ev)
        pending = "pending_review" in classes
        parts = []
        if age >= 0:
            parts.append(f"{age} days old" if age != 1 else "1 day old")
        parts.append(_due_phrase(d.get("due"), now_iso))
        parts.append("task (yours)" if kind == "task" else kind)
        if cid in stale_ids:
            parts.append("still on your plate?")
        if pending and d.get("review_reason"):
            parts.append(_display_review_reason(ws, d.get("review_reason"),
                                                rr_cache))
        # SUB1 D5/D6 — parent progress chip + orphan note (loader stamps).
        kids = subs_by_parent.get(cid) or []
        n_open_k = d.get("n_subitems_open")
        n_done_k = d.get("n_subitems_done")
        if kids or isinstance(n_open_k, int):
            total_k = (n_open_k or 0) + (n_done_k or 0)
            chip = f"sub-items {n_done_k or 0}/{total_k}"
            nxt = _next_open_child(kids)
            if nxt is not None:
                chip += f" · next: {nxt}"
            parts.append(chip)
        if d.get("parent_closed") and d.get("parent_title"):
            parts.append(f"was part of: {d['parent_title']}")
        row = {
            "n": cid, "display_n": display_n,
            "name": d.get("title") or d.get("summary") or "(untitled)",
            "context_tag": " · ".join(parts),
            "actions": (_PENDING_VERBS if pending
                        else (_TASK_VERBS if kind == "task" else _PROMISE_VERBS)),
        }
        if pending:
            row["reduced_verbs_reason"] = _REDUCED_REASON
        # SUB1 D3 — the PROPOSE-closure line (never auto-close): renders on
        # the parent row when the last open child closed.
        if d.get("all_subitems_resolved"):
            row["annotations"] = ["all sub-items done — close it?"]
        # SUB1 D6 — nested child rows: id = the child's data.id VERBATIM
        # (identity contract, Stage B); per-kind dropdown minus the
        # non-child verbs. Only OPEN children render (done ones are the
        # chip's numerator).
        if kids:
            row["sub_items"] = []
            for k in kids:
                kd = k.get("data") or {}
                k_kind = commitment_kind(k)
                summary = kd.get("title") or "(untitled)"
                if kd.get("due"):
                    summary += f" — {_due_phrase(kd.get('due'), now_iso)}"
                row["sub_items"].append({
                    "id": _cid(k),
                    "summary": summary,
                    "actions": (_CHILD_TASK_VERBS if k_kind == "task"
                                else _CHILD_PROMISE_VERBS),
                })
        return row

    def _next_open_child(kids: list) -> str | None:
        """The step to name in the progress chip: the open child with the
        earliest parseable effective due, else the first in append order."""
        if not kids:
            return None
        best_ev, best_date = None, None
        for k in kids:
            kd = k.get("data") or {}
            try:
                kd_date = _dt.date.fromisoformat(str(kd.get("due"))[:10])
            except (ValueError, TypeError):
                kd_date = None
            if kd_date is not None and (best_date is None or kd_date < best_date):
                best_ev, best_date = k, kd_date
        pick = best_ev or kids[0]
        return (pick.get("data") or {}).get("title") or None

    old_rows = [_row(ev, age) for age, ev in aged if age >= 30]
    new_rows = [_row(ev, age) for age, ev in aged if age < 30]
    if old_rows:
        sections.append({"title": "30+ days old", "count": len(old_rows),
                         "items": old_rows})
    if new_rows:
        sections.append({"title": "The rest", "count": len(new_rows),
                         "items": new_rows})

    h = counts["headline"]
    counters = [
        {"label": "Open", "value": h["total"]},
        {"label": "You owe", "value": h["you_owe"]},
        {"label": "Owed to you", "value": h["owed_to_you"]},
        {"label": "Unowned", "value": h["unowned"]},
        {"label": "Unconfirmed", "value": h["unconfirmed"]},
    ]
    # SUB1 D6 — tiles unchanged in shape (values are top-level per D2); when
    # sub-items exist the HEADER appends the additive key — never a new tile
    # that implies a fifth bucket.
    header = f"Commitment triage — {h['total']} open, oldest first"
    if h.get("subitems_open"):
        header += f" (+{h['subitems_open']} sub-items)"
    return {
        "source_skill": "commitment-triage",
        "header": header,
        "counters": counters,
        "sections": sections,
    }


def build_staff_meeting_view(workspace_root, *, now_iso: str | None = None,
                             moves_rows: list | None = None) -> dict:
    """The Staff Meeting queue view (orchestrator Phase 3+5, mechanized):
    THE projector + D3 ranking + build_card_view. `moves_rows` (Phase 4's
    email-shaped rows, connector-dependent so built by the orchestrator) are
    appended as the THIS WEEK'S MOVES section when supplied."""
    from brain_proposals import (build_card_view, load_open_proposals,
                                 rank_proposals)

    queue = rank_proposals(load_open_proposals(
        workspace_root, "staff-meeting", now_iso=now_iso))
    extra = ([{"title": "THIS WEEK'S MOVES", "items": list(moves_rows)}]
             if moves_rows else None)
    return build_card_view(queue, surface="staff-meeting",
                           extra_sections=extra)


def build_waiting_on_view(workspace_root, *, now_iso: str | None = None,
                          chase_rows: list | None = None) -> dict:
    """The daily Waiting On chat data view (CTS1 Surface 1 —
    orchestrator-commitments, mechanized): canonical loader + `surface_split`
    five-way partition + the count headline + the deterministic delegated and
    confirm-tail sections. Pure read.

    `chase_rows` (the pre-staged chase-email items for the CRU-eligible
    owed-to-you commitments — connector-dependent, so built by the orchestrator
    exactly like build_staff_meeting_view's `moves_rows`) are appended VERBATIM
    as the leading "Waiting On" section; the driver never composes an email body
    or touches a connector. Everything else — the partition, the header counts,
    the delegated-task rows, and the unowned/unconfirmed confirm tail — is
    deterministic and built here, killing the ~30-command surface the
    orchestrator assembled live (FB-15). Owner-me rows never surface here (they
    are My Plate — the partition routes them away)."""
    from commitment_activity import derive_commitment_movement
    from commitment_state import count_commitments
    from confirm_flow import unconfirmed_classes
    from cru_match import load_open_commitments
    from primary_user import resolve_primary_user
    from surface_split import (SURFACE_UNCONFIRMED, SURFACE_UNOWNED,
                               SURFACE_WAITING_ON, effective_kind_of,
                               partition_surfaces)

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    events_path = _events_path(ws)
    opens = load_open_commitments(events_path)
    try:
        user_id = resolve_primary_user(ws)
    except Exception:
        user_id = None
    movement = derive_commitment_movement(events_path)
    counts = count_commitments(opens, user_person_id=user_id,
                               now_iso=now_iso, movement=movement)
    part = partition_surfaces(opens, user_id)

    def _cid(ev) -> str:
        d = ev.get("data") or {}
        return d.get("id") or f"commitment_seq_{ev.get('seq')}"

    display_n = 0
    sections: list[dict] = []
    rr_cache: dict = {}  # RRF1 per-render-pass memo (name -> resolves now)

    # 1. Chase drafts — orchestrator-supplied (connector-dependent), appended
    #    verbatim: email-shaped rows whose pre-staged nudge lives in the widget.
    if chase_rows:
        rows = []
        for r in chase_rows:
            display_n += 1
            row = dict(r)
            row.setdefault("display_n", display_n)
            rows.append(row)
        sections.append({"title": "Waiting On", "count": len(rows),
                         "items": rows})

    # 2. Delegated tasks (owner != user, effective kind `task`) — CRU-ineligible,
    #    so no pre-staged chase; the manual action set (`draft` composes a nudge
    #    on demand) per orchestrator-commitments §2.3.
    delegated = [ev for ev in part[SURFACE_WAITING_ON]
                 if effective_kind_of(ev) == "task"]
    if delegated:
        people_by_id = _people_by_id(ws)
        rows = []
        for ev in delegated:
            display_n += 1
            d = ev.get("data") or {}
            age = _age_days(ev.get("ts") or "", now_iso)
            bits = []
            if age is not None and age >= 0:
                bits.append("1 day old" if age == 1 else f"{age} days old")
            bits.append(_due_phrase(d.get("due"), now_iso))
            # A delegated row is owner != M by definition — name who we're
            # waiting on (FIX A); fall back to bare "delegated" only if no name
            # resolves.
            owner_name = _owner_display_name(ev, people_by_id)
            bits.append(
                f"delegated to {owner_name} — nudge is manual, "
                "I won't auto-chase this" if owner_name
                else "delegated — nudge is manual, I won't auto-chase this")
            # `draft` composes the nudge on demand at dispatch (no pre-staged
            # body) — but it is send-class (renderer Gate 6 / Bug #44), so the
            # row must carry a valid To:. Resolve the owner's email; when none
            # is on file, degrade `draft` to the `add email then send` recovery
            # verb so the surface still renders (never a dead button).
            email = _owner_email(ev, people_by_id)
            row: dict = {
                "n": _cid(ev), "display_n": display_n,
                "name": d.get("title") or d.get("summary") or "(untitled)",
                "context_tag": " · ".join(bits),
            }
            if email:
                row["metadata"] = [["To", email]]
                row["actions"] = list(_DELEGATED_VERBS)
            else:
                row["actions"] = ["add email then send"] + [
                    v for v in _DELEGATED_VERBS if v != "draft"]
            rows.append(row)
        sections.append({"title": "Delegated", "count": len(rows),
                         "items": rows})

    # 3. Confirm tail — unowned + pending_review (unconfirmed). The REVIEW
    #    cluster only: NEVER a pre-staged chase on an unconfirmed/unowned item
    #    (no auto-email on a guessed owner — orchestrator-commitments §458/§478).
    confirm_evs = list(part[SURFACE_UNOWNED]) + list(part[SURFACE_UNCONFIRMED])
    if confirm_evs:
        rows = []
        for ev in confirm_evs:
            display_n += 1
            d = ev.get("data") or {}
            pending = "pending_review" in unconfirmed_classes(ev)
            tag = ("captured from a chat — confirm it's yours" if pending
                   else "no owner resolved yet — whose is this?")
            if d.get("review_reason"):
                tag += f" · {_display_review_reason(ws, d['review_reason'], rr_cache)}"
            rows.append({
                "n": _cid(ev), "display_n": display_n,
                "name": d.get("title") or d.get("summary") or "(untitled)",
                "context_tag": tag,
                "actions": list(_REVIEW_VERBS),
            })
        sections.append({"title": "Needs a quick confirm", "count": len(rows),
                         "items": rows})

    h = counts["headline"]
    counters = [
        {"label": "Owed to you", "value": h["owed_to_you"]},
        {"label": "Unowned", "value": h["unowned"]},
        {"label": "Unconfirmed", "value": h["unconfirmed"]},
    ]
    return {
        "source_skill": "commitments",
        "header": f"Waiting On — {h['owed_to_you']} owed to you",
        "counters": counters,
        "sections": sections,
    }


def _last_brief_ts(workspace_root, now_iso: str) -> str:
    """The CHANGED window's opening edge: the newest prior morning-brief
    receipt's timestamp, else the newest `brief_state` event's ts, else
    36 hours back (first-ever brief — one day plus slack so an overnight
    install still gets a real window, never an empty-string scan)."""
    from event_time import parse_ts
    from receipts import iter_receipts

    try:
        receipts = iter_receipts(workspace_root, task_ids=["morning-brief"])
        dts = [r["dt"] for r in receipts if r.get("dt") is not None]
        if dts:
            return max(dts).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    try:
        import json as _json
        newest = None
        p = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if '"brief_state"' not in line:
                    continue
                try:
                    ev = _json.loads(line)
                except Exception:
                    continue
                if ev.get("type") == "brief_state" and ev.get("ts"):
                    newest = ev["ts"]
        if newest:
            return newest
    except Exception:
        pass
    base = parse_ts(now_iso)
    return (base - _dt.timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_morning_brief_pack(workspace_root, *, mode: str = "scheduled",
                             now_iso: str | None = None) -> dict:
    """t3 FB-9 — the morning brief's mandatory substrate blocks, assembled,
    validated, and persisted in ONE call (the t2.2 skip-proofing pattern
    that fixed commitments/staff-meeting). A live post-update fire skipped
    the brain-card widget AND the substrate-alarm line while the pre-update
    fire rendered both — instruction-layer MUSTs (FS-09) don't survive an
    orchestrator that stops early; a driver whose single output carries
    every block does.

    Returns (and persists to `_hq/.system/briefs/`) the pack:

      alarm_lines    substrate_health.substrate_alarm_lines — render
                     VERBATIM at the very top of the brief (FS-04/05/06/15).
      changed        change_feed.changes_since(<last brief ts>) — the lines
                     the CHANGED contract line MUST cite when non-empty
                     (FS-09; "Nothing material" over a non-empty feed is
                     the bug).
      brief_state    compute_and_log_brief_state's counts headline +
                     needs_attention + reconcile_stale. THE one Step-3d
                     derivation — the driver call writes the `brief_state`
                     audit event, so the orchestrator must NOT call it
                     again (one event per fire).
      watchdog_line  task_watchdog.brief_watchdog_line — append verbatim
                     when non-None (S3 light pass).
      money_lines    FB-20's ONE carve-out: money-class proposals (deal
                     signals) as one prose sentence each, propose-only —
                     "Command Room thinks [Org] is a live deal — say staff
                     meeting to confirm." Money is the one class that may
                     never go silent, so it is NAMED in the brief; the
                     adjudication still happens at the staff meeting, by
                     chat phrase, with no widget. Capped at
                     MONEY_PROSE_CAP (the pointer's count carries the rest —
                     a cap is a render bound, never a silence).
      queue_pointer  {count, line} — the live count of everything the staff
                     meeting would render, and the ONE pointer line that
                     replaces the card ("N things need your eyes — say
                     staff meeting"). count == what `staff meeting` actually
                     shows (same projector, same surface, same held/mute
                     filters), so the number can never over-promise. Zero →
                     empty line, nothing renders (drop-empty).

    FB-20 (M's ruling 2026-07-16 — "the morning brief should just be a
    morning brief"): this pack emits NO widget and NO confirm card. The
    brief is a READ-ONLY prose surface; the staff meeting is the sole
    adjudication surface (run it more often instead). A `transport` key from
    this driver is a contract violation — the T3.2 relay machinery stays
    intact for every OTHER surface, but the brief has exited the widget
    business entirely. There is nothing to relay, so nothing can be dropped
    on the way to the relay (the FB-18 failure mode is gone by construction,
    not by instruction).

    Every text line in the pack passes the chat-output leak scan before
    return — a leaking canonical line fails HERE, loudly, not in M's chat.
    Connector-dependent digest content (calendar, mail, Slack) remains the
    orchestrator's job; this pack is the substrate half — the half that
    kept getting skipped.
    """
    from chat_output_renderer import validate_chat_output
    from change_feed import changes_since
    from commitment_state import compute_and_log_brief_state
    from cru_match import load_open_commitments
    from primary_user import resolve_primary_user
    from substrate_health import substrate_alarm_lines
    from task_watchdog import brief_watchdog_line

    if mode not in ("scheduled", "manual"):
        raise ValueError(f"mode must be scheduled|manual; got {mode!r}")
    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()

    alarm_lines = list(substrate_alarm_lines(ws) or [])

    since_ts = _last_brief_ts(ws, now_iso)
    feed = changes_since(ws, since_ts, now_iso=now_iso, max_lines=3)
    changed_lines = [l.get("text", "") for l in (feed.get("lines") or [])
                     if l.get("text")]

    opens = load_open_commitments(_events_path(ws))
    try:
        user_id = resolve_primary_user(ws)
    except Exception:
        user_id = None
    state = compute_and_log_brief_state(
        ws, open_commitments=opens, user_person_id=user_id, now_iso=now_iso)
    brief_state = {
        "headline": (state.get("counts") or {}).get("headline") or {},
        "needs_attention": state.get("needs_attention") or [],
        "reconcile_stale": state.get("reconcile_stale"),
    }

    try:
        watchdog = brief_watchdog_line(ws)
    except Exception:
        watchdog = None

    # FB-20 — the queue POINTER (not the queue). The brief names no rows and
    # renders no card; it points at the surface that adjudicates. The count
    # comes from the staff-meeting projection so the pointer's promise and
    # what `staff meeting` renders are the same number by construction (an
    # over-promising count is its own dishonesty — the FS-09 class).
    from brain_proposals import load_open_proposals, money_prose_lines
    queue = load_open_proposals(ws, "staff-meeting", now_iso=now_iso)
    # Auto-tier items are applied-then-narrated, never adjudicated (LB1
    # review F5) — they are not "things that need your eyes". Redundant
    # since LB2 flipped the projector default (include_auto=False) — kept
    # as defense-in-depth; the parity pin tests the projector, not this.
    queue = [i for i in queue if i.get("tier") != "auto"]
    money_lines = money_prose_lines(queue, cap=MONEY_PROSE_CAP)
    queue_pointer = {"count": len(queue), "line": _pointer_line(len(queue))}

    # Leak-scan every text line the pack hands the orchestrator. Loud by
    # design — there is no widget validator behind this one any more.
    scannable = "\n".join(
        alarm_lines + changed_lines + ([watchdog] if watchdog else [])
        + money_lines
        + ([queue_pointer["line"]] if queue_pointer["line"] else [])
    )
    if scannable.strip():
        validate_chat_output(scannable)

    pack = {
        "surface": "morning-brief",
        "mode": mode,
        "now": now_iso,
        "alarm_lines": alarm_lines,
        "changed": {"since_ts": since_ts, "lines": changed_lines},
        "brief_state": brief_state,
        "watchdog_line": watchdog,
        "money_lines": money_lines,
        "queue_pointer": queue_pointer,
    }

    # Persist the pack (audit trail, parallel to the widget audit files).
    try:
        from atomic_write import atomic_write_text
        out_dir = ws / "_hq" / ".system" / "briefs"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_iso[:19].replace(":", "-")
        atomic_write_text(out_dir / f"morning-pack-{stamp}.json",
                          json.dumps(pack, indent=2, ensure_ascii=False))
    except Exception:
        pass  # the pack in hand is what matters; the audit copy is best-effort

    return pack


# ---------------------------------------------------------------------------
# Fire receipts (FB-7 — the receipt writes INSIDE the driver call)
# ---------------------------------------------------------------------------

# surface -> the canonical receipts.py task its fire receipts belong to.
_SURFACE_TASKS = {"commitments": "commitment-triage",
                  "staff-meeting": "staff-meeting",
                  "waiting-on": "waiting-on"}   # FB-15 (CTS1 taskId)

# RV-3 guard: a NON-MANUAL driver re-run this close to an already-written
# non-manual receipt is the same fire re-rendering (the live 2026-07-16
# staff-meeting double-render), not a second run — one receipt, never two.
# Manual fires never dedup: two back-to-back manual sweeps are two real
# runs (F-08).
_REFIRE_RECEIPT_GUARD = _dt.timedelta(minutes=15)


def _log_fire_receipt(workspace_root, surface: str, view: dict,
                      fired_via: str) -> dict:
    """Append the surface's per-fire pack_run receipt via the canonical
    helper (receipts.log_receipt — NEVER hand-rolled JSON). Runs inside
    run_surface's page-1 invocation so the widget render and the receipt
    can never be separated (FB-7: the scheduled staff-meeting fire posted
    its widget, then the turn ended before the prose receipt step)."""
    from receipts import iter_receipts, log_receipt, normalize_fired_via

    task_id = _SURFACE_TASKS[surface]
    via = normalize_fired_via(fired_via)
    surfaced = sum(len(sec.get("items") or [])
                   for sec in view.get("sections") or [])
    if via != "manual":
        now = _dt.datetime.now(_dt.timezone.utc)
        recent = iter_receipts(workspace_root, task_ids=[task_id],
                               since=now - _REFIRE_RECEIPT_GUARD)
        if any(r["fired_via"] != "manual" for r in recent):
            return {"task_id": task_id, "fired_via": via,
                    "surfaced": surfaced, "status": "deduped_refire"}
    log_receipt(workspace_root, task_id, fired_via=via, surfaced=surfaced)
    return {"task_id": task_id, "fired_via": via, "surfaced": surfaced,
            "status": "written"}


def run_surface(surface: str, workspace_root, *, page: int = 1,
                page_size: int = 15, now_iso: str | None = None,
                moves_rows: list | None = None,
                chase_rows: list | None = None,
                fired_via: str | None = None) -> dict:
    """Build the view + render_and_persist ONE page. Returns the transport
    dict (html / pagination / path). The CLI wraps this; tests call it
    directly.

    `fired_via` (scheduled | manual | catchup — the orchestrator's detected
    run mode, Phase 2.9 `receipt_fired_via`) makes the PAGE-1 invocation
    also write the surface's canonical per-fire receipt inside this same
    call (see _log_fire_receipt; the written/deduped outcome rides back on
    transport["receipt"]). Pages 2+ never receipt; omitting fired_via
    renders only (legacy callers unchanged)."""
    from widget_transport import render_and_persist

    if fired_via is not None:
        from receipts import FIRED_VIA, normalize_fired_via
        if normalize_fired_via(fired_via) not in FIRED_VIA:
            raise ValueError(
                f"fired_via must be one of {sorted(FIRED_VIA)}; "
                f"got {fired_via!r}")

    ws = Path(workspace_root)
    if surface == "commitments":
        view = build_commitment_triage_view(ws, now_iso=now_iso)
        name_hint = "commitment-triage"
    elif surface == "staff-meeting":
        view = build_staff_meeting_view(ws, now_iso=now_iso,
                                        moves_rows=moves_rows)
        name_hint = "staff-meeting"
    elif surface == "waiting-on":
        view = build_waiting_on_view(ws, now_iso=now_iso,
                                     chase_rows=chase_rows)
        name_hint = "waiting-on"
    else:
        raise SystemExit(f"unknown surface {surface!r} "
                         "(supported: commitments, staff-meeting, waiting-on)")
    transport = render_and_persist(
        data_view=view,
        wrapper="fragment",
        persist_dir=ws / "_hq" / ".system" / "widgets",
        name_hint=name_hint,
        page=page,
        page_size=page_size,
    )
    if fired_via is not None and page == 1:
        transport["receipt"] = _log_fire_receipt(ws, surface, view, fired_via)
    return transport


def main() -> int:
    # Review F-5: Windows pipes default to cp1252 — the output carries
    # non-ASCII (middots, warning glyphs) and would crash the CLI.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("surface",
                    choices=["commitments", "staff-meeting", "waiting-on",
                             "morning-brief"])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--mode", default="scheduled",
                    choices=["scheduled", "manual"],
                    help="morning-brief only: scheduled renders the confirm "
                         "card as a widget page; manual renders markdown "
                         "lines (t3 FB-9)")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--page-size", type=int, default=15,
                    help="requested rows/page ceiling (the byte-fit may lower it)")
    ap.add_argument("--now", default=None, help="ISO now override (tests)")
    ap.add_argument("--moves-json", default=None,
                    help="staff-meeting only: JSON file with the Phase-4 "
                         "moves rows (email-shaped item dicts)")
    ap.add_argument("--chase-json", default=None,
                    help="waiting-on only: JSON file with the pre-staged chase "
                         "rows (email-shaped item dicts, connector-dependent)")
    ap.add_argument("--fired-via", default=None,
                    choices=["scheduled", "manual", "catchup"],
                    help="the fire's run mode (the orchestrator's Phase-2.9 "
                         "receipt_fired_via); when given, the page-1 "
                         "invocation also writes the surface's canonical "
                         "per-fire receipt inside this call (FB-7)")
    args = ap.parse_args()

    if args.surface == "morning-brief":
        # t3 FB-9 — ONE call, every mandatory block. The orchestrator places
        # each emitted block; the CR-BRIEF-PACK line is the checklist.
        # FB-20: this surface emits PROSE ONLY — no widget block, no relay
        # banner, nothing to post to show_widget. The brief is read-only by
        # construction. (The banner + CR-WIDGET-HTML markers below this
        # branch still serve commitments / staff-meeting unchanged.)
        pack = build_morning_brief_pack(args.workspace, mode=args.mode,
                                        now_iso=args.now)
        print("CR-BRIEF-PACK: " + json.dumps(pack, ensure_ascii=False))
        return 0

    moves_rows = None
    if args.moves_json:
        moves_rows = json.loads(Path(args.moves_json).read_text(encoding="utf-8"))
    chase_rows = None
    if args.chase_json:
        chase_rows = json.loads(Path(args.chase_json).read_text(encoding="utf-8"))

    transport = run_surface(
        args.surface, args.workspace, page=args.page,
        page_size=args.page_size, now_iso=args.now, moves_rows=moves_rows,
        chase_rows=chase_rows, fired_via=args.fired_via)

    pagination = transport.get("pagination") or {}
    print("CR-PAGINATION: " + json.dumps(pagination))
    print("CR-WIDGET-HTML-BEGIN")
    print(transport["html"])
    print("CR-WIDGET-HTML-END")
    receipt = transport.get("receipt")
    if receipt is not None:
        print("CR-RECEIPT: " + json.dumps(receipt))
    return 0


__all__ = [
    "build_commitment_triage_view",
    "build_morning_brief_pack",
    "build_staff_meeting_view",
    "build_waiting_on_view",
    "run_surface",
]


if __name__ == "__main__":
    raise SystemExit(main())
