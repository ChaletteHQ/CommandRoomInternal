#!/usr/bin/env python3
"""Atomically rewrite ONE marked region inside a brain file, byte-preserving
everything outside the markers. The render half of the brain-substrate fix:
the generated "Live State" block redraws from substrate; the durable
hand-owned narrative around it is never touched.

Marker convention (HTML comments, invisible in rendered markdown):

    <!-- LIVE-STATE:people generated_at=2026-05-30T12:00:00Z source_seq=2453 -->
    ...generated content...
    <!-- /LIVE-STATE:people -->

Contract:
  - render_block(): replaces content between the start/end markers for
    `block_id`. Durable content outside is byte-identical. Idempotent — an
    unchanged render rewrites nothing and reports "unchanged".
  - If the markers are absent, it does NOT guess an insertion point unless
    `create_after_heading` is given (used by the one-time migration). Otherwise
    it returns status "no_anchor" so a caller never silently mangles a file.
  - read_block_meta() + needs_render() power the load-path dirty-check: skip
    the rewrite unless a thread-tagged event newer than the block's recorded
    source_seq exists. (C9 proved cadence-only rots; this makes the load path
    cheap enough to fire every time.)

stdlib only (+ atomic_write for fsync/rename safety).
"""

from __future__ import annotations

import re
from pathlib import Path

from atomic_write import atomic_write_text


def _markers(block_id: str):
    start = re.compile(
        r"<!--\s*LIVE-STATE:" + re.escape(block_id) + r"\b[^>]*-->",
        re.IGNORECASE)
    end = re.compile(
        r"<!--\s*/LIVE-STATE:" + re.escape(block_id) + r"\s*-->",
        re.IGNORECASE)
    return start, end


def _start_marker(block_id: str, generated_at: str | None, source_seq,
                  logic_version=None) -> str:
    bits = [f"LIVE-STATE:{block_id}"]
    if generated_at:
        bits.append(f"generated_at={generated_at}")
    if source_seq is not None:
        bits.append(f"source_seq={source_seq}")
    if logic_version is not None:
        bits.append(f"logic_v={logic_version}")
    return f"<!-- {' '.join(bits)} -->"


def read_block_meta(file_path: str | Path, block_id: str) -> dict | None:
    """Return {'generated_at':..., 'source_seq':int|None, 'logic_v':int|None}
    from the block's start marker, or None if the block isn't present.

    `logic_v` is the render-logic version the block was last built under (Bug
    #97). A block written before the stamp existed has logic_v=None, which the
    dirty-check treats as "stale logic" so the next sweep re-renders it once."""
    p = Path(file_path)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    start, _ = _markers(block_id)
    m = start.search(text)
    if not m:
        return None
    tag = m.group(0)
    ga = re.search(r"generated_at=(\S+)", tag)
    ss = re.search(r"source_seq=(\d+)", tag)
    lv = re.search(r"logic_v=(\d+)", tag)
    return {"generated_at": ga.group(1) if ga else None,
            "source_seq": int(ss.group(1)) if ss else None,
            "logic_v": int(lv.group(1)) if lv else None}


def needs_render(file_path: str | Path, block_id: str, latest_event_seq,
                 logic_version=None) -> bool:
    """Dirty-check: render needed if the block is missing, has no recorded
    source_seq, a newer thread-tagged event exists than it was built from, OR
    the render logic changed since the block was built (Bug #97).

    The render-logic-version check is what reaches QUIET threads: a project with
    no new events keeps its old source_seq forever, so a source_seq-only check
    never re-renders it — a logic change (e.g. the #87 umbrella-bleed filter)
    would never reach a frozen brain. When the caller passes a `logic_version`
    that differs from the block's recorded `logic_v` (None for pre-stamp blocks),
    the block is stale-by-logic and re-renders once; afterwards its stamp matches
    and it goes quiet again (no churn)."""
    meta = read_block_meta(file_path, block_id)
    if meta is None or meta.get("source_seq") is None:
        return True
    if logic_version is not None and meta.get("logic_v") != logic_version:
        return True
    if latest_event_seq is None:
        return False
    return latest_event_seq > meta["source_seq"]


def render_block(file_path: str | Path, block_id: str, body: str, *,
                 generated_at: str | None = None, source_seq=None,
                 logic_version=None,
                 create_after_heading: str | None = None) -> dict:
    """Replace the `block_id` region with `body`. Returns
    {'status': 'written'|'unchanged'|'created'|'no_anchor'}.

    Durable content outside the markers is preserved byte-for-byte.
    `logic_version` (Bug #97) stamps the render-logic version into the start
    marker so a later sweep can re-render when the logic changes, not only when
    new events arrive.
    """
    p = Path(file_path)
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    start_re, end_re = _markers(block_id)
    start_tag = _start_marker(block_id, generated_at, source_seq, logic_version)
    end_tag = f"<!-- /LIVE-STATE:{block_id} -->"
    block = f"{start_tag}\n{body.rstrip()}\n{end_tag}"

    sm = start_re.search(text)
    em = end_re.search(text)
    if sm and em and em.start() > sm.start():
        new_text = text[:sm.start()] + block + text[em.end():]
        if new_text == text:
            return {"status": "unchanged"}
        atomic_write_text(p, new_text)
        return {"status": "written"}

    # No (valid) markers present.
    if create_after_heading:
        lines = text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.strip() == create_after_heading.strip():
                insert = ("" if line.endswith("\n") else "\n") + "\n" + block + "\n"
                lines.insert(i + 1, insert)
                atomic_write_text(p, "".join(lines))
                return {"status": "created"}
        # heading not found — append a fresh block at end
        sep = "" if text.endswith("\n") or text == "" else "\n"
        atomic_write_text(p, text + sep + "\n" + block + "\n")
        return {"status": "created"}

    return {"status": "no_anchor"}
