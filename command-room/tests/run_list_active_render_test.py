#!/usr/bin/env python3
"""Regression guard for list-active rendering canonical_name (v4.0.0 dogfood Bug 1).

Real `entities.json` stores org/thread names ONLY under `canonical_name`
(`name`/`display_name` are absent or None). render_tree's label fallback was
`display_name or name or id`, so every node fell through to the raw id — the
CEO saw `org_001` / `project_005` instead of `Chalette` / `Command Room`.
The unit fixtures used `name`, so the battery never caught it.

stdlib only.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "list-active"))

import render_tree  # noqa: E402


def test_canonical_name_only_renders_name_not_id() -> None:
    # Real-shaped: name lives ONLY under canonical_name.
    entities = {
        "orgs": [{"id": "org_001", "canonical_name": "Chalette", "is_primary_focus": True}],
        "threads": [
            {"id": "project_005", "canonical_name": "Command Room Build",
             "status": "active", "affiliation_id": "org_001"}
        ],
    }
    root_orgs, projects = render_tree.build_tree(entities, [], None, False)
    out = render_tree.render(root_orgs, projects)

    assert "Chalette" in out, f"org canonical_name not rendered:\n{out}"
    assert "Command Room Build" in out, f"thread canonical_name not rendered:\n{out}"
    assert "org_001" not in out, f"raw org id leaked into output:\n{out}"
    assert "project_005" not in out, f"raw project id leaked into output:\n{out}"
    print("PASS test_canonical_name_only_renders_name_not_id")


def test_display_name_still_wins_when_present() -> None:
    # Back-compat: an explicit display_name/name still takes precedence.
    entities = {
        "orgs": [{"id": "org_002", "display_name": "Explicit Co", "canonical_name": "Ignored"}],
        "threads": [],
    }
    root_orgs, projects = render_tree.build_tree(entities, [], None, False)
    out = render_tree.render(root_orgs, projects)
    assert "Explicit Co" in out and "Ignored" not in out, out
    print("PASS test_display_name_still_wins_when_present")


def main() -> int:
    test_canonical_name_only_renders_name_not_id()
    test_display_name_still_wins_when_present()
    print("ALL list-active render tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
