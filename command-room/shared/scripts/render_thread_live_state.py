#!/usr/bin/env python3
"""Orchestrator: render a thread's "Live State" block (People + Status) into its
brain file from the substrate. Ties together thread_roster (who's on it),
entities.json (status), and render_brain_block (safe marked-region write).

This is the deterministic callable workspace-manager invokes on `go [project]`
and cleanup invokes on its sweep — so the live state is produced by code, not
by the LLM assembling it freehand (the enforcement-gate failure class).

Dirty-check: only rewrites when a thread-tagged event NEWER than the block's
recorded source_seq exists. The "latest seq" uses the human-counter filter
(ignores legacy nano-epoch seqs ≥ 1e10) so a stray 1.77e18 artifact can't peg
the check (gotcha caught during the build demo).

stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

import event_refs
import render_brain_block
from thread_roster import derive_roster

EPOCH_THRESHOLD = 10 ** 10
BLOCK_ID = "people"
# The heading the block is created after on first render (self-bootstrap).
DEFAULT_ANCHOR = "## 1. People"

# Render-logic version (Bug #97). BUMP THIS whenever the *content logic* below
# (which people are confirmed/proposed, how the block reads) changes — NOT for
# new data, which the source_seq dirty-check already catches. A bump makes the
# next sweep re-render even QUIET threads whose source_seq hasn't moved, so a
# logic fix actually reaches frozen brains instead of rotting behind a stale
# block. History: v1 = pre-stamp blocks (no logic_v in marker); v2 = v3.18.9
# #87 re-fix (org-association applied to low+inherited proposed candidates).
LIVE_STATE_LOGIC_VERSION = 2


def _entities(workspace_root: Path) -> dict:
    data = json.loads((workspace_root / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    return data["entities"] if isinstance(data.get("entities"), dict) else data


def _human_latest_seq(events: list[dict], thread_ids: set[str]) -> int | None:
    """Newest human-counter seq among events touching `thread_ids`. Ignores
    nano-epoch legacy seqs (≥ 1e10) so the dirty-check can't misfire."""
    latest = None
    for ev in events:
        if not (event_refs.threads_of(ev) & thread_ids):
            continue
        s = event_refs.event_seq(ev)
        if s is None or s >= EPOCH_THRESHOLD:
            continue
        s = int(s)
        if latest is None or s > latest:
            latest = s
    return latest


def _thread(workspace_root: Path, thread_id: str) -> dict:
    ent = _entities(workspace_root)
    threads = ent.get("threads", []) or ent.get("projects", [])
    return next((t for t in threads if t.get("id") == thread_id), {})


def _org_associated_pids(ent: dict, org_id: str) -> set[str]:
    """Person ids affiliated with `org_id` via primary_org_id, affiliation_ids,
    or the deprecated org_id field (v3.13.0+ shapes, defensively)."""
    out: set[str] = set()
    if not org_id:
        return out
    for p in ent.get("people", []) or []:
        pid = p.get("id")
        if not pid:
            continue
        affs = set(p.get("affiliation_ids") or [])
        if (p.get("primary_org_id") == org_id
                or p.get("org_id") == org_id      # deprecated, still honored
                or org_id in affs):
            out.add(pid)
    return out


def default_brain_path(workspace_root: str | Path, thread_id: str) -> Path | None:
    """Resolve the thread's brain file from its folder_name. None if unknown."""
    workspace_root = Path(workspace_root)
    folder = _thread(workspace_root, thread_id).get("folder_name")
    if not folder:
        return None
    return workspace_root / folder / "PROJECT_BRAIN.md"


def format_live_state(workspace_root: str | Path, thread_id: str):
    """Return (markdown_body, source_seq). Pure — no file writes."""
    workspace_root = Path(workspace_root)
    thread = _thread(workspace_root, thread_id)
    status = thread.get("status", "?")
    events = event_refs.load_events(workspace_root / "_hq" / "data" / "events.jsonl")
    n_events = sum(1 for e in events if thread_id in event_refs.threads_of(e))
    source_seq = _human_latest_seq(events, {thread_id} | _lineage(thread))

    roster = derive_roster(workspace_root, thread_id)
    confirmed = [r for r in roster if r["confidence"] in ("high", "pinned")]

    # Proposed (confirm-gate) set = the `low` + `inherited` candidates, filtered
    # by ORG-ASSOCIATION. This is the v3.18.9 #87 re-fix.
    #
    # The umbrella-bleed (Bug #87, v3.18.1): a pre-split umbrella's whole roster
    # gets proposed onto every sub-thread, surfacing people who aren't on this
    # project (visible to the CEO since the Bug #86 fix renders this line). The
    # v3.18.4 fix org-filtered ONLY `inherited` candidates — but real bleed isn't
    # `inherited`. On a live workspace the noise is `low`-confidence: vendor/demo
    # contacts with a single direct project-local event (n_direct=1) and no org
    # affiliation, which the old code waved straight through (`low` returned True
    # unconditionally). The earlier assumption that "low always has legitimate
    # project signal" was wrong — one stray demo event is enough to make a contact
    # `low`, and that contact is exactly the noise the CEO doesn't want proposed.
    #
    # Correct rule: a `low`/`inherited` person is proposed only if they're
    # affiliated with THIS thread's org. That keeps the genuine org-mates who have
    # light signal and drops the org-less demo/vendor contacts. If the thread has
    # no org_id we can't discriminate, so we keep all candidates (the safe,
    # lineage-aware direction — don't silently drop real umbrella members).
    org_id = thread.get("org_id") or ""
    org_pids = _org_associated_pids(_entities(workspace_root), org_id) if org_id else set()

    def _propose(r):
        if r["confidence"] not in ("low", "inherited"):
            return False
        if not org_id:
            return True  # degenerate: no thread org to discriminate by
        return r["person_id"] in org_pids

    proposed = [r for r in roster if _propose(r)]

    lines = [f"**Status:** {status}    *(live from substrate — {n_events} events)*", ""]
    lines.append("| Person | Events | Last seen |")
    lines.append("|---|---|---|")
    for r in confirmed:
        lines.append(f"| {r['name']} | {r['n_events']} | {r['last_ts'] or '—'} |")
    if not confirmed:
        lines.append("| _(no confirmed members yet)_ |  |  |")
    if proposed:
        names = ", ".join(r["name"] for r in proposed)
        lines.append("")
        lines.append(f"*Proposed — confirm to add (inherited from a pre-split umbrella or low signal):* {names}")
    return "\n".join(lines), source_seq


def _lineage(thread: dict) -> set[str]:
    out = set()
    for k in ("parent_thread_id", "spawned_from_thread_id"):
        v = thread.get(k)
        if isinstance(v, str) and v:
            out.add(v)
    return out


def render_live_state(workspace_root: str | Path, thread_id: str, *,
                      brain_path: str | Path | None = None,
                      anchor: str = DEFAULT_ANCHOR, force: bool = False) -> dict:
    """Dirty-check then render the Live State block. Returns the render_block
    status plus {'rendered': bool, 'source_seq': int|None}."""
    workspace_root = Path(workspace_root)
    path = Path(brain_path) if brain_path else default_brain_path(workspace_root, thread_id)
    if path is None:
        return {"status": "no_brain_path", "rendered": False, "source_seq": None}

    body, source_seq = format_live_state(workspace_root, thread_id)
    if not force and not render_brain_block.needs_render(
            path, BLOCK_ID, source_seq, logic_version=LIVE_STATE_LOGIC_VERSION):
        return {"status": "unchanged", "rendered": False, "source_seq": source_seq}

    import datetime
    ga = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    r = render_brain_block.render_block(
        path, BLOCK_ID, body, generated_at=ga, source_seq=source_seq,
        logic_version=LIVE_STATE_LOGIC_VERSION, create_after_heading=anchor)
    r["rendered"] = r["status"] in ("written", "created")
    r["source_seq"] = source_seq
    return r


if __name__ == "__main__":
    import sys
    ws, tid = sys.argv[1], sys.argv[2]
    bp = sys.argv[3] if len(sys.argv) > 3 else None
    body, seq = format_live_state(ws, tid)
    print(f"# source_seq={seq}\n{body}")
