#!/usr/bin/env python3
"""Tests for render_brain_block.py (brain-substrate-drift build).

The whole safety case for rendering into a hand-owned file rests on: durable
content outside the markers stays byte-identical, renders are idempotent, a
missing anchor never mangles the file, and the dirty-check fires correctly.

stdlib only.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from render_brain_block import render_block, read_block_meta, needs_render  # noqa: E402

results = {"pass": 0, "fail": 0}


def check(name, cond):
    if cond:
        results["pass"] += 1; print(f"PASS {name}")
    else:
        results["fail"] += 1; print(f"FAIL {name}")


def _f(text):
    p = Path(tempfile.mkdtemp(prefix="cr-rbb-test-")) / "PROJECT_BRAIN.md"
    p.write_text(text, encoding="utf-8")
    return p


DURABLE = """# Project Brain

## Durable judgment (hand-owned — do not auto-edit)
This paragraph must never be touched by the renderer. Special chars: é, →, `code`.

## 1. Live State
<!-- LIVE-STATE:people source_seq=10 -->
OLD CONTENT
<!-- /LIVE-STATE:people -->

## 2. More durable notes
Keep me exactly as I am.
"""


def test_replaces_only_block_and_preserves_durable():
    p = _f(DURABLE)
    r = render_block(p, "people", "NEW ROSTER LINE", source_seq=12)
    text = p.read_text(encoding="utf-8")
    check("status written", r["status"] == "written")
    check("new content present", "NEW ROSTER LINE" in text)
    check("old content gone", "OLD CONTENT" not in text)
    check("durable header preserved", "## Durable judgment (hand-owned — do not auto-edit)" in text)
    check("durable special chars preserved", "é, →, `code`" in text)
    check("trailing durable preserved", "Keep me exactly as I am." in text)


def test_idempotent():
    p = _f(DURABLE)
    render_block(p, "people", "SAME", source_seq=12)
    r2 = render_block(p, "people", "SAME", source_seq=12)
    check("second identical render is unchanged", r2["status"] == "unchanged")


def test_no_anchor_does_not_mangle():
    p = _f("# Brain\n\nNo markers here at all.\n")
    before = p.read_text(encoding="utf-8")
    r = render_block(p, "people", "X")
    check("no_anchor status", r["status"] == "no_anchor")
    check("file untouched when no anchor", p.read_text(encoding="utf-8") == before)


def test_create_after_heading():
    p = _f("# Brain\n\n## 1. Live State\n\n## 2. Durable\nkeep\n")
    r = render_block(p, "people", "FRESH", source_seq=5, create_after_heading="## 1. Live State")
    text = p.read_text(encoding="utf-8")
    check("created status", r["status"] == "created")
    check("block inserted after heading", "LIVE-STATE:people" in text and "FRESH" in text)
    check("durable below preserved", "## 2. Durable\nkeep" in text)


def test_dirty_check():
    p = _f(DURABLE)  # block source_seq=10
    check("read_block_meta seq", read_block_meta(p, "people")["source_seq"] == 10)
    check("needs_render false when not newer", needs_render(p, "people", 10) is False)
    check("needs_render true when newer event", needs_render(p, "people", 11) is True)
    check("needs_render true when block missing", needs_render(p, "status", 1) is True)


def test_logic_version_dirty_check():
    """Bug #97: a logic-version bump re-renders a QUIET block (no new events),
    then the block goes quiet again — and logic_version=None preserves the old
    source_seq-only behavior for callers that don't opt in."""
    # DURABLE's block has source_seq=10 and NO logic_v stamp (a pre-#97 block).
    p = _f(DURABLE)
    check("pre-stamp block has logic_v None", read_block_meta(p, "people").get("logic_v") is None)
    # No new events (latest seq == block seq) but logic moved 1 -> re-render.
    check("stale-logic block re-renders even with no new events",
          needs_render(p, "people", 10, logic_version=1) is True)
    # Backward-compat: without a logic_version, behave exactly as before.
    check("logic_version=None keeps source_seq-only behavior",
          needs_render(p, "people", 10) is False)

    # Render under logic_version=2, then the SAME version must NOT churn.
    r = render_block(p, "people", "ROSTER V2", source_seq=10, logic_version=2)
    check("render writes logic_v into marker", r["status"] == "written")
    check("read_block_meta returns logic_v", read_block_meta(p, "people").get("logic_v") == 2)
    check("matching logic_version + no new events -> no re-render (no churn)",
          needs_render(p, "people", 10, logic_version=2) is False)
    # A further bump re-renders again (once).
    check("a further logic bump re-renders",
          needs_render(p, "people", 10, logic_version=3) is True)
    # A new event still re-renders regardless of logic version.
    check("newer event still re-renders under matching logic_version",
          needs_render(p, "people", 11, logic_version=2) is True)


def main():
    test_replaces_only_block_and_preserves_durable()
    test_idempotent()
    test_no_anchor_does_not_mangle()
    test_create_after_heading()
    test_dirty_check()
    test_logic_version_dirty_check()
    print(f"=== {results['pass']} passed, {results['fail']} failed ===")
    return 1 if results["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
