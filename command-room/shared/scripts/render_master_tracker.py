#!/usr/bin/env python3
"""
Master-tracker view regenerator (v4.2.0+).

Deterministically regenerates `_hq/views/MASTER_TRACKER.md` (+ the back-compat
copy `_hq/MASTER_TRACKER.md`) from `_hq/data/entities.json` (threads + orgs) and
`_hq/data/events.jsonl` (activity events), per the MASTER_TRACKER template in
`references/VIEW_GENERATION.md` and the org-tree layout contract in
`skills/morning-briefing/SKILL.md` Step 4.

WHY THIS EXISTS:

MASTER_TRACKER.md is a *generated* view but — exactly like PEOPLE.md before
`render_people_view.py` and DECISION_LOG.md before `render_decision_log.py` —
it had NO code generator. `references/VIEW_GENERATION.md` and
`skills/workspace-manager/SKILL.md` both claimed a "writer helper regenerates
`_hq/views/MASTER_TRACKER.md` … after every change," but no such code existed:
the tracker was hand-rendered by the LLM during end-session. When the model
stopped hand-rendering, the view froze while the substrate kept flowing
(forensic case: M's tracker frozen since 2026-06-11 while entities.json /
events.jsonl stayed current). It was the only major projected view with no
deterministic renderer and no cleanup Phase 3.5 backstop.

This renderer makes the tracker deterministic; end-session calls `regenerate`
and cleanup's weekly sweep calls `regenerate_if_changed` so the view never
falls behind canonical substrate. Wrapper-aware (nested-vs-flat entities shape)
and — critically — every commitment field is read through
`cru_match._commitment_field` / `_commitment_confidence`, NOT raw
`data.owner_id` / `data.description`. Direct field reads silently drop the 4
non-canonical commitment shape variants seen in production (~42% of commitments
in some workspaces — the v3.4.4 bug class extended to view-regen).

PUBLIC API:

  - regenerate(workspace_root) -> dict
      Read entities.json + events.jsonl, render the view, atomic-write
      `_hq/views/MASTER_TRACKER.md` (+ back-compat `_hq/MASTER_TRACKER.md`).
      Returns counts. Idempotent (content-stable apart from the timestamp).

  - regenerate_if_changed(workspace_root) -> dict
      Changed-only regeneration (SPEC CLEAN1 / D4). Build the candidate view,
      compare it (ignoring volatile timestamp lines) against what's on disk,
      write ONLY if the tracker content actually changed. cleanup calls this
      every weekly run so a missed regen never persists for weeks while a quiet
      workspace stays a true no-op write. Returns counts plus `changed` (bool).

USAGE:

    python3 shared/scripts/render_master_tracker.py <workspace_root>

    # Or from another skill:
    from render_master_tracker import regenerate
    counts = regenerate(workspace_root)
"""
from __future__ import annotations

import datetime
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import events_io  # noqa: E402
from atomic_write import atomic_write_text  # noqa: E402
from thread_activity import ALL_TYPES, derive_from_events  # noqa: E402

# Shape-safe commitment reads (REQUIRED per VIEW_GENERATION.md MASTER_TRACKER
# section). load_open_commitments returns open, not-yet-closed commitment events;
# _commitment_field / _commitment_confidence coerce the 5 shape variants.
from cru_match import (  # noqa: E402
    load_open_commitments,
    split_pending_review,
    _commitment_field,
    _commitment_confidence,
)
# The one counting API (Phase 2 Stage A) — the tracker's open-commitments
# headline MUST be count_commitments(...)["total"], the same number the brief,
# the coach, and commitment_counts() report. The pre-Stage-A tracker reported
# the confidence-FILTERED row count as its headline, one of the three diverging
# aggregators in the 2026-07-01 audit (104 vs 54 vs 105).
from commitment_state import count_commitments  # noqa: E402

# Optional tz localization for activity dates. Fall back to ts[:10] if missing.
try:
    from tz import to_local  # noqa: E402
    _HAS_TZ = True
except ImportError:
    _HAS_TZ = False

CONFIDENCE_FLOOR = 0.40

# Relationship-type ordering for the OTHER ORGS rollup. Mirrors the order in
# VIEW_GENERATION.md MASTER_TRACKER template + morning-briefing Step 4 rule 2.
REL_TYPE_ORDER = [
    "operating", "partner", "client", "board", "advisory", "investment",
    "portfolio_company", "beneficiary", "service_provider", "vendor",
    "prospect", "other",
]

# Thread statuses that get their own sections, not the per-org active tables.
_PAUSED_BLOCKED = ("paused", "blocked")


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _entities_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "entities.json"


# LB2 §3e — event reads route through events_io (shard-transparent): on a
# rotated workspace a thread whose last activity lives in a yearly shard used
# to read as inactive/dormant here while the workspace map showed it live
# (the F-54 divergence class, extended to rotation). Full iteration by
# default — last-activity derivation must see full history. The old private
# active-file-only loader is deleted, not wrapped.


def _load_collections(p: Path) -> dict:
    """Wrapper-aware: return the dict holding threads/orgs/people regardless of
    nested-under-`entities` vs flat top-level shape (mirrors render_people_view)."""
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data.get("entities") if isinstance(data.get("entities"), dict) else data


def _localize_date(ts: str | None, workspace_path: str | None = None) -> str:
    """ISO timestamp → workspace-local date string. Date-only inputs pass
    through unchanged. Falls back to ts[:10] if tz localization is unavailable.
    Mirrors render_decision_log._localize_date."""
    if not isinstance(ts, str) or not ts:
        return ""
    if len(ts) == 10 and ts.count("-") == 2:
        return ts
    if _HAS_TZ and workspace_path:
        try:
            local_dt = to_local(ts, workspace_path=workspace_path)
            if local_dt:
                return local_dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return ts[:10]


def _name_index(view: dict) -> dict[str, str]:
    """Flat id → display name across orgs, threads/projects, people."""
    idx: dict[str, str] = {}
    for o in view.get("orgs") or []:
        if isinstance(o, dict) and o.get("id"):
            idx[o["id"]] = o.get("canonical_name") or o["id"]
    for coll in ("threads", "projects"):
        for t in view.get(coll) or []:
            if isinstance(t, dict) and t.get("id"):
                idx[t["id"]] = (
                    t.get("display_name") or t.get("canonical_name")
                    or t.get("folder_name") or t["id"]
                )
    for pr in view.get("people") or []:
        if isinstance(pr, dict) and pr.get("id"):
            idx[pr["id"]] = pr.get("canonical_name") or pr["id"]
    return idx


def _thread_org_id(t: dict) -> str | None:
    """Thread → org link. Canonical field is `org_id` (ENTITY1 — the majority
    field in the fleet; `affiliation_id` is a legacy alias normalised to
    `org_id` on write, still read here for records that predate the collapse);
    then the people-style fields for shape-defensiveness."""
    return t.get("org_id") or t.get("affiliation_id") or t.get("primary_org_id") or (
        (t.get("affiliation_ids") or [None])[0]
    )


def _threads(view: dict) -> list[dict]:
    coll = view.get("threads")
    if not isinstance(coll, list) or not coll:
        coll = view.get("projects") or []
    return [t for t in coll if isinstance(t, dict)]


def _badge(o: dict | None) -> str:
    """relationship_type badge, e.g. `[board]`. Omitted for the default
    operating + primary-focus case (reduces noise) per VIEW_GENERATION.md."""
    if not o:
        return ""
    rt = o.get("relationship_type")
    if not rt:
        return ""
    if rt == "operating" and o.get("is_primary_focus"):
        return ""
    return f"[{rt.replace('_', ' ')}]"


def regenerate(workspace_root: str | Path) -> dict[str, Any]:
    """Read entities.json + events.jsonl, render MASTER_TRACKER.md, atomic-write
    the view (+ back-compat copy). Returns counts. Idempotent apart from the
    regenerated-at header."""
    workspace_root = Path(workspace_root)
    content, counts = _build_content(workspace_root)

    view_path = workspace_root / "_hq" / "views" / "MASTER_TRACKER.md"
    view_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(view_path, content)
    try:  # back-compat copy at _hq/MASTER_TRACKER.md
        atomic_write_text(workspace_root / "_hq" / "MASTER_TRACKER.md", content)
    except Exception:
        pass

    counts["view_path"] = str(view_path)
    return counts


def _build_content(workspace_root: Path) -> tuple[str, dict[str, Any]]:
    """Build MASTER_TRACKER.md content + counts WITHOUT writing. Factored out so
    regenerate_if_changed can compare candidate content against disk before
    deciding whether to write (idempotence)."""
    ws_str = str(workspace_root)
    view = _load_collections(_entities_path(workspace_root))
    events = events_io.load_all(workspace_root)  # shard-transparent (LB2 §3e)
    name_idx = _name_index(view)

    orgs = [o for o in (view.get("orgs") or []) if isinstance(o, dict)]
    threads = _threads(view)
    org_by_id = {o["id"]: o for o in orgs if o.get("id")}

    # --- last-activity per thread (C3 consolidated rule) ----------------------
    # derive_from_events with ALL_TYPES: renderer "last touched" semantics
    # (every event type counts, as this scan always did), but the thread-id
    # resolution is the settled C3 rule — related_thread_ids and the legacy
    # data-level spellings count too. The old hand-rolled loop matched
    # primary_thread_id only, so a cross-referenced meeting bumped the
    # workspace map and stalled-projects but not this column (the F-54
    # same-project-two-numbers divergence class).
    # honor_reclassifications (RECL1): corrections move the original event's
    # credit; under ALL_TYPES the reclassification event ITSELF also bumps
    # the corrected thread at correction time — correct "last touched"
    # renderer semantics (pinned in tests).
    last_act: dict[str, str] = {
        tid: act.ts.isoformat()
        for tid, act in derive_from_events(
            events, activity_types=ALL_TYPES, honor_reclassifications=True
        ).items()
    }

    def thread_last_activity(t: dict) -> str:
        # Zero-event floor per the C3 deprecation carve-out: the stored
        # last_activity stamp may be consulted only when a thread has no
        # events (fresh-ingest records), then first_seen — same chain as
        # entity_resolve / stall_detector / workspace-map / DCC.
        tid = t.get("id") or ""
        return _localize_date(
            last_act.get(tid) or t.get("last_activity") or t.get("first_seen") or "",
            ws_str,
        )

    # --- org tree helpers ----------------------------------------------------
    def children_of(oid: str) -> list[dict]:
        return sorted(
            [o for o in orgs if o.get("parent_org_id") == oid],
            key=lambda o: (o.get("canonical_name") or "").lower(),
        )

    def descendant_ids(oid: str, include_descendants: bool = True) -> set[str]:
        ids = {oid}
        if include_descendants:
            frontier = [oid]
            while frontier:
                cur = frontier.pop()
                for c in orgs:
                    cid = c.get("id")
                    if cid and c.get("parent_org_id") == cur and cid not in ids:
                        ids.add(cid)
                        frontier.append(cid)
        return ids

    def active_threads_for(orgids: set[str]) -> list[dict]:
        out = [
            t for t in threads
            if _thread_org_id(t) in orgids
            and t.get("status") not in ("archived",) + _PAUSED_BLOCKED
        ]
        return sorted(out, key=thread_last_activity, reverse=True)

    def org_recency(oid: str) -> str:
        ts = [thread_last_activity(t) for t in active_threads_for(descendant_ids(oid))]
        return max(ts) if ts else ""

    def threads_table(orgids: set[str]) -> list[str]:
        rows = active_threads_for(orgids)
        if not rows:
            return []
        out = [
            "| Thread | Kind | Status | Stage | Last Activity | Next Step | Owner |",
            "|---|---|---|---|---|---|---|",
        ]
        for t in rows:
            owner = name_idx.get(t.get("owner_person_id") or "", "") or "—"
            out.append(
                f"| {name_idx.get(t.get('id', ''), t.get('display_name') or t.get('id') or '—')} "
                f"| {t.get('kind') or '—'} | {t.get('status') or '—'} | {t.get('stage') or '—'} "
                f"| {thread_last_activity(t) or '—'} | {t.get('next_step') or '—'} | {owner} |"
            )
        out.append("")
        return out

    body: list[str] = []

    # --- Primary-focus org sections -----------------------------------------
    primary = sorted(
        [o for o in orgs if o.get("is_primary_focus") and not o.get("parent_org_id")],
        key=lambda o: org_recency(o.get("id", "")),
        reverse=True,
    )
    rendered_orgs: set[str] = set()
    for o in primary:
        oid = o.get("id") or ""
        kids = children_of(oid)
        is_holding = o.get("scope") == "holding" and bool(kids)
        # Skip an org with no active threads anywhere in its subtree.
        if not active_threads_for(descendant_ids(oid)):
            continue
        body += [f"## {o.get('canonical_name') or oid} {_badge(o)}".rstrip(), ""]
        rendered_orgs |= descendant_ids(oid)
        if is_holding:
            for c in sorted(kids, key=lambda c: org_recency(c.get("id", "")), reverse=True):
                tbl = threads_table(descendant_ids(c.get("id", "")))
                if tbl:
                    body += [f"### {c.get('canonical_name') or c.get('id')} {_badge(c)}".rstrip(), ""] + tbl
            holding_only = threads_table(descendant_ids(oid, include_descendants=False))
            if holding_only:
                body += ["### Holding-level threads", ""] + holding_only
        else:
            body += threads_table(descendant_ids(oid))

    # --- Other Orgs rollup ---------------------------------------------------
    other: list[str] = []
    for rt in REL_TYPE_ORDER:
        rt_orgs = sorted(
            [o for o in orgs
             if not o.get("is_primary_focus")
             and o.get("relationship_type") == rt
             and active_threads_for(descendant_ids(o.get("id", "")))],
            key=lambda o: org_recency(o.get("id", "")),
            reverse=True,
        )
        if not rt_orgs:
            continue
        section = [f"### {rt.replace('_', ' ').title()}", ""]
        for o in rt_orgs:
            n = len(active_threads_for(descendant_ids(o.get("id", ""))))
            la = org_recency(o.get("id", "")) or "—"
            section.append(f"- **{o.get('canonical_name') or o.get('id')}** — {n} active, last activity {la}")
        section.append("")
        other += section
    # Untyped / off-enum bucket (ENTITY1 §3): the enum is advisory by design,
    # so an org whose relationship_type is off-enum (or absent) must still
    # render — silent invisibility is the defect, not the free-text value.
    untyped = sorted(
        [o for o in orgs
         if not o.get("is_primary_focus")
         and o.get("relationship_type") not in REL_TYPE_ORDER
         and active_threads_for(descendant_ids(o.get("id", "")))],
        key=lambda o: org_recency(o.get("id", "")),
        reverse=True,
    )
    if untyped:
        section = ["### Untyped / needs attention", ""]
        for o in untyped:
            n = len(active_threads_for(descendant_ids(o.get("id", ""))))
            la = org_recency(o.get("id", "")) or "—"
            rt = o.get("relationship_type")
            label = f" (relationship_type: `{rt}`)" if rt else " (no relationship_type)"
            section.append(
                f"- **{o.get('canonical_name') or o.get('id')}**{label} — "
                f"{n} active, last activity {la}")
        section.append("")
        other += section
    if other:
        body += ["## Other Orgs", ""] + other

    # --- Paused / Blocked (across all orgs) ----------------------------------
    pb = sorted(
        [t for t in threads if t.get("status") in _PAUSED_BLOCKED],
        key=thread_last_activity, reverse=True,
    )
    if pb:
        body += [
            "## Paused / Blocked (across all orgs)", "",
            "| Thread | Org | Status | Last Activity | Reason |",
            "|---|---|---|---|---|",
        ]
        for t in pb:
            org = org_by_id.get(_thread_org_id(t) or "")
            body.append(
                f"| {name_idx.get(t.get('id', ''), t.get('display_name') or '—')} "
                f"| {(org or {}).get('canonical_name') or '—'} | {t.get('status')} "
                f"| {thread_last_activity(t) or '—'} | {t.get('blocked_reason') or t.get('reason') or '—'} |"
            )
        body.append("")

    # --- Recently Archived (top 10) ------------------------------------------
    archived = sorted(
        [t for t in threads if t.get("status") == "archived"],
        key=lambda t: t.get("archived_at") or "", reverse=True,
    )[:10]
    if archived:
        body += [
            "## Recently Archived", "",
            "| Thread | Org | Archived | Reason |", "|---|---|---|---|",
        ]
        for t in archived:
            org = org_by_id.get(_thread_org_id(t) or "")
            body.append(
                f"| {name_idx.get(t.get('id', ''), t.get('display_name') or '—')} "
                f"| {(org or {}).get('canonical_name') or '—'} | {(t.get('archived_at') or '—')[:10]} "
                f"| {t.get('archive_reason') or '—'} |"
            )
        body.append("")

    # --- Open Commitments (shape-safe via cru_match) -------------------------
    # INTAKE — unconfirmed extractions are queue members, not open
    # commitments: out of the rows AND out of the headline.
    open_commitments, needs_review = split_pending_review(
        load_open_commitments(_events_path(workspace_root)))
    shown = [c for c in open_commitments if _commitment_confidence(c) >= CONFIDENCE_FLOOR]
    provisional = len(open_commitments) - len(shown)

    def _due_key(ev: dict) -> str:
        d = _commitment_field(ev, "due")
        return d if isinstance(d, str) and d else "9999-12-31"

    shown.sort(key=_due_key)
    # Headline = the canonical total from the one counting API (Stage A). The
    # confidence floor only decides which ROWS render in the table below —
    # provisional items are still open commitments and stay in the headline
    # (reporting len(shown) here was the tracker's Bug-#85-class divergence).
    open_count = count_commitments(open_commitments)["total"]
    if shown:
        body += [
            "## Open Commitments (across all threads)", "",
            "| Description | Owner | Due | Thread | Org | Status |",
            "|---|---|---|---|---|---|",
        ]
        for ev in shown[:20]:
            tid = ev.get("primary_thread_id") or ""
            thread = next((t for t in threads if t.get("id") == tid), None)
            org = org_by_id.get(_thread_org_id(thread) or "") if thread else None
            owner = name_idx.get(_commitment_field(ev, "owner_id") or "", "") or "—"
            body.append(
                f"| {_commitment_field(ev, 'title') or '—'} | {owner} "
                f"| {_commitment_field(ev, 'due') or '—'} | {name_idx.get(tid, '—')} "
                f"| {(org or {}).get('canonical_name') or '—'} "
                f"| {_commitment_field(ev, 'status') or 'open'} |"
            )
        body.append("")
    if provisional:
        body += [
            f"> _{provisional} open commitment(s) are on events with "
            f"classification_confidence < 0.40 or pending review — not shown above. "
            f"Run `insight-generator` to review._", "",
        ]
    if needs_review:
        # INTAKE — the excluded queue, named once. Not a commitment count.
        body += [
            f"> _Needs your call: {len(needs_review)} unconfirmed "
            f"extraction(s) — say `needs your call` to confirm or drop them. "
            f"They are not counted as open commitments above._", "",
        ]

    # --- Assemble ------------------------------------------------------------
    now_iso = datetime.datetime.now().replace(microsecond=0).isoformat()
    primary_count = sum(1 for o in primary if active_threads_for(descendant_ids(o.get("id", ""))))

    # Fail-loud guard (ENTITY1 §2): if active threads point at a primary-focus
    # org by ANY known field spelling but the resolver attributed none of them,
    # the view is silently wrong — an empty primary section is indistinguishable
    # from "nothing going on there". Raise instead of emitting it. (This is how
    # the reference workspace rendered its primary org and all eight projects
    # nowhere: records carried `org_id` alone and the chain couldn't see it.)
    if primary and primary_count == 0:
        primary_subtree_ids: set[str] = set()
        for o in primary:
            primary_subtree_ids |= descendant_ids(o.get("id", ""))
        raw_refs = {
            ref
            for t in threads
            if t.get("status") not in ("archived",) + _PAUSED_BLOCKED
            for ref in (
                t.get("org_id"), t.get("affiliation_id"), t.get("primary_org_id"),
                (t.get("affiliation_ids") or [None])[0],
            )
            if ref
        }
        missed = raw_refs & primary_subtree_ids
        if missed:
            raise RuntimeError(
                "MASTER_TRACKER render would silently drop the primary-focus "
                f"org(s): active threads reference {sorted(missed)} but the "
                "org-link resolver attributed none of them (primary_orgs would "
                "be 0). Refusing to write a wrong view — check "
                "_thread_org_id's field chain against the thread records."
            )
    active_thread_count = len([
        t for t in threads
        if t.get("status") not in ("archived",) + _PAUSED_BLOCKED
    ])
    header = [
        "<!-- AUTO-GENERATED by shared/scripts/render_master_tracker.py — do not edit by hand. -->",
        f"<!-- regenerated-at: {now_iso} -->",
        f"<!-- totals: {active_thread_count} active threads, {open_count} open commitments -->",
        "<!-- source: _hq/data/entities.json + events.jsonl -->",
        "",
        "# Master Tracker",
        "",
        f"_{active_thread_count} active threads · {open_count} open commitments · "
        f"regenerated {now_iso}_",
        "",
        "---",
        "",
    ]
    content = "\n".join(header + body).rstrip() + "\n"
    counts = {
        "active_threads": active_thread_count,
        "primary_orgs": primary_count,
        "paused_blocked": len(pb),
        "archived": len(archived),
        "open_commitments": open_count,
        "provisional_commitments": provisional,
    }
    return content, counts


# Header lines that change on every render (timestamps) even when the tracker
# content is identical. Stripped before the changed-only comparison so a quiet
# workspace is a true no-op write. Mirrors render_decision_log._strip_volatile.
def _strip_volatile(text: str) -> str:
    out = []
    for line in text.splitlines():
        if "regenerated-at:" in line:
            continue
        if line.startswith("_") and "· regenerated " in line:
            continue
        out.append(line)
    return "\n".join(out)


def regenerate_if_changed(workspace_root: str | Path) -> dict[str, Any]:
    """Changed-only regeneration (SPEC CLEAN1 / D4). Build the candidate view,
    compare (ignoring volatile timestamp lines) against disk, write ONLY if the
    tracker content actually changed — and keep the back-compat copy in sync.
    Returns the counts dict plus `changed` (bool)."""
    workspace_root = Path(workspace_root)
    view_path = workspace_root / "_hq" / "views" / "MASTER_TRACKER.md"
    compat_path = workspace_root / "_hq" / "MASTER_TRACKER.md"
    content, counts = _build_content(workspace_root)

    old = view_path.read_text(encoding="utf-8") if view_path.exists() else ""
    compat_missing = not compat_path.exists()
    changed = _strip_volatile(old) != _strip_volatile(content)
    if changed or compat_missing:
        view_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(view_path, content)
        try:
            atomic_write_text(compat_path, content)
        except Exception:
            pass

    counts["changed"] = changed or compat_missing
    counts["view_path"] = str(view_path)
    return counts


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python3 render_master_tracker.py <workspace_root>", file=sys.stderr)
        return 2
    ws = Path(argv[1])
    if not ws.exists():
        print(f"ABORT: workspace not found: {ws}", file=sys.stderr)
        return 2
    r = regenerate(ws)
    print(
        f"OK — regenerated MASTER_TRACKER.md "
        f"({r['active_threads']} active threads, {r['open_commitments']} open commitments)"
    )
    print(f"  written to: {r['view_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
