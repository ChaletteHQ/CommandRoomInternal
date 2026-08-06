#!/usr/bin/env python3
"""Regenerate CLAUDE.md's org + workstream sections from entities.json
(CLAUDEMD1 Defect A — the always-on file never syncs forward).

CLAUDE.md is loaded into context at the start of every session — the
highest-leverage file in a workspace and the only one paid for on every
turn. It was authored by hand at onboarding and nothing ever updated it: the
reference workspace listed 5 orgs while entities.json held 21, and orgs
converted to client by pipeline-tracker still read "prospect" in the prose.

This reuses the PROJECT_BRAIN generated-block contract (render_brain_block)
rather than inventing a second convention:

    <!-- LIVE-STATE:orgs generated_at=... source_seq=... logic_v=... -->
    ...generated register...
    <!-- /LIVE-STATE:orgs -->

Hand-authored prose outside the markers — voice rules, session rules,
preferences — is preserved byte-for-byte (that is render_block's contract,
pinned in its own tests and re-pinned in run_claudemd1_test.py).

Terseness is load-bearing: one line per org / workstream, plus one line
naming how many records were excluded and where the full register lives.
Passive-tier and archived orgs are excluded from the register (counted, so
hidden never reads as deleted); archived threads likewise.

SOFT CAP (the CLAUDEMD1 rider). Terse per org is still unbounded: a workspace
whose prospecting sweeps mint hundreds of active orgs pays the whole register
as a per-session context tax. Above SOFT_CAP_ORGS visible orgs, only the ones
worth a line each — clients, partners, the primary-focus org, and any org
carrying an open deal — render individually; the rest collapse to counts by
relationship_type. Same honesty rule as the exclusion counts: collapsed never
reads as deleted, the counts sum to the collapsed total, and the full register
is one line away. At or below the cap the output is byte-identical to the
uncapped render — the cap adds nothing until it engages.

Determinism is a hard requirement, not a nicety: the file is dirty-check-gated
on `source_seq`, so any per-run variance would rewrite CLAUDE.md every session.
Every ordering here is a total sort on record data.

stdlib only (+ the shared renderer/writer helpers).
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from entities_io import entities_collection          # noqa: E402
from next_seq import next_seq                        # noqa: E402
from render_brain_block import needs_render, render_block  # noqa: E402
from thread_writer import thread_org_id              # noqa: E402

LOGIC_VERSION = 1
BLOCK_ORGS = "orgs"
BLOCK_WORKSTREAMS = "workstreams"
SOURCE_LINE = "_Full register: `_hq/data/entities.json` · views: `_hq/views/`_"

# Above this many VISIBLE orgs (post passive/archived exclusion) the register
# collapses everything that isn't relationship-critical. Tuned to the fleet:
# the largest real workspace sits well under it, so today nobody sees the cap.
SOFT_CAP_ORGS = 40

# Always worth its own line once the cap engages — the relationships the CEO
# is actually operating, plus the workspace's own org. Everything else is
# reachable in one hop from the register pointer.
ALWAYS_LISTED_RELATIONSHIPS = frozenset({"client", "partner"})

# Reader-facing plurals for the collapsed counts. Off-enum values (M's live
# workspace carries a legacy `network`) render verbatim — the register reports
# the data, it does not correct it.
_RELATIONSHIP_PLURALS = {
    "client": "clients",
    "prospect": "prospects",
    "partner": "partners",
    "vendor": "vendors",
    "investment": "investments",
    "beneficiary": "beneficiaries",
    "portfolio_company": "portfolio companies",
    "service_provider": "service providers",
}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _load(ws: Path) -> dict:
    data = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    return data


def _latest_seq(ws: Path) -> int | None:
    events_path = ws / "_hq" / "data" / "events.jsonl"
    if not events_path.exists():
        return None
    try:
        return max(0, next_seq(str(events_path)) - 1)
    except Exception:
        return None


def _open_deal_org_ids(data: dict) -> set:
    """Orgs carrying an OPEN deal thread — the same notion of "open" the deal
    writer and the twin check use (non-terminal status, no recorded outcome).
    Read-only and defensive: this is a renderer, not a writer."""
    out = set()
    threads = (entities_collection(data, "threads")
               or entities_collection(data, "projects"))
    for t in threads:
        if not isinstance(t, dict) or t.get("kind") != "deal":
            continue
        if t.get("status") in ("resolved", "archived"):
            continue
        if (t.get("deal") or {}).get("outcome"):
            continue
        oid = thread_org_id(t)
        if oid:
            out.add(oid)
    return out


def _relationship_plural(rel: str, count: int) -> str:
    if count == 1:
        return rel
    return _RELATIONSHIP_PLURALS.get(rel, rel)


def _collapsed_counts_phrase(collapsed: list) -> str:
    """"31 prospects, 12 network" — deterministic: biggest group first, ties
    broken by relationship_type name."""
    counts: dict = {}
    for rel in collapsed:
        counts[rel] = counts.get(rel, 0) + 1
    return ", ".join(
        f"{n} {_relationship_plural(rel, n)}"
        for rel, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _org_body(data: dict) -> str:
    orgs = [o for o in entities_collection(data, "orgs") if isinstance(o, dict)]
    visible, hidden = [], 0
    for o in sorted(orgs, key=lambda o: (not o.get("is_primary_focus"),
                                         (o.get("canonical_name") or "").lower())):
        if o.get("status") == "archived" or o.get("tier") == "passive":
            hidden += 1
            continue
        visible.append(o)

    # The soft cap engages only ABOVE the threshold — at or below it, every
    # visible org still gets its line and the tail is byte-identical to the
    # pre-cap render.
    capped = len(visible) > SOFT_CAP_ORGS
    deal_orgs = _open_deal_org_ids(data) if capped else set()

    listed, collapsed = [], []
    for o in visible:
        rel = o.get("relationship_type") or "untyped"
        if capped and not (rel in ALWAYS_LISTED_RELATIONSHIPS
                           or o.get("is_primary_focus")
                           or o.get("id") in deal_orgs):
            collapsed.append(rel)
            continue
        flags = ", primary focus" if o.get("is_primary_focus") else ""
        listed.append(f"- {o.get('canonical_name') or o.get('id')} — {rel}{flags}")

    # "(none yet)" means the workspace has no orgs — it must never stand in for
    # "they all collapsed", which would read as deleted. With a collapse in play
    # the counts line is the honest body.
    if listed:
        body_lines = listed
    elif collapsed:
        body_lines = []
    else:
        body_lines = ["- (none yet)"]
    tail = f"_{len(listed)} listed"
    if collapsed:
        tail += (f"; {len(collapsed)} more not shown individually "
                 f"({_collapsed_counts_phrase(collapsed)})")
    if hidden:
        tail += f"; {hidden} passive/archived not shown"
    tail += f"._ {SOURCE_LINE}"
    lines = ["## Orgs", ""] + (body_lines + [""] if body_lines else [])
    return "\n".join(lines + [tail])


def _workstream_body(data: dict) -> str:
    orgs = {o.get("id"): o for o in entities_collection(data, "orgs")
            if isinstance(o, dict)}
    threads = [t for t in (entities_collection(data, "threads")
                           or entities_collection(data, "projects"))
               if isinstance(t, dict)]
    listed, hidden = [], 0
    for t in sorted(threads, key=lambda t: (t.get("canonical_name")
                                            or t.get("display_name") or "").lower()):
        if t.get("status") == "archived":
            hidden += 1
            continue
        org = orgs.get(thread_org_id(t) or "")
        org_bit = f" ({org.get('canonical_name')})" if org else ""
        status = t.get("status") or "active"
        listed.append(
            f"- {t.get('canonical_name') or t.get('display_name') or t.get('id')}"
            f"{org_bit} — {status}")
    lines = ["## Workstreams", ""] + (listed or ["- (none yet)"])
    tail = f"_{len(listed)} listed"
    if hidden:
        tail += f"; {hidden} archived not shown"
    tail += f"._ {SOURCE_LINE}"
    return "\n".join(lines + ["", tail])


def needs_regenerate(workspace_root: str | Path) -> bool:
    ws = Path(workspace_root)
    claude_md = ws / "CLAUDE.md"
    if not claude_md.exists():
        return False
    seq = _latest_seq(ws)
    return any(
        needs_render(claude_md, b, seq, logic_version=LOGIC_VERSION)
        for b in (BLOCK_ORGS, BLOCK_WORKSTREAMS))


def regenerate(workspace_root: str | Path) -> dict:
    """Redraw both generated blocks in <workspace>/CLAUDE.md. Returns per-block
    statuses. A CLAUDE.md without markers gets the blocks appended at the end
    (render_block's created path) — it is never rewritten wholesale, and a
    missing CLAUDE.md is left missing (onboarding owns first authorship)."""
    ws = Path(workspace_root)
    claude_md = ws / "CLAUDE.md"
    if not claude_md.exists():
        return {"status": "no_claude_md"}
    data = None
    seq = _latest_seq(ws)
    now = _now_iso()
    out = {}
    for block_id, body_fn in ((BLOCK_ORGS, _org_body),
                              (BLOCK_WORKSTREAMS, _workstream_body)):
        # Honor the dirty-check BEFORE rebuilding (v5.10.0 flake fix): the
        # start marker carries a second-resolution `generated_at`, so an
        # unconditional rebuild only compares equal when both calls land in
        # the SAME second — across a second tick a no-op regenerate rewrote
        # the whole file with nothing but a new timestamp (and read as
        # "written"). needs_render is the contract's actual dirty signal
        # (missing block / source_seq advanced / logic_v changed); when it
        # says clean, the block IS current and the file is not touched.
        if not needs_render(claude_md, block_id, seq,
                            logic_version=LOGIC_VERSION):
            out[block_id] = "unchanged"
            continue
        if data is None:
            data = _load(ws)
        out[block_id] = render_block(
            claude_md, block_id, body_fn(data),
            generated_at=now, source_seq=seq, logic_version=LOGIC_VERSION,
            # Marker-less files (every pre-CLAUDEMD1 workspace): the heading
            # never exists, so render_block appends the block at the end —
            # deterministic adoption with zero risk to hand-authored prose.
            create_after_heading="## Workspace register (generated)",
        )["status"]
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    print(json.dumps(regenerate(sys.argv[1]), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
