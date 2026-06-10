#!/usr/bin/env python3
"""
Helper for the `scaffold-automation` skill (v3.8.0+).

Provides:
- `slugify(title)` — deterministic kebab-case slug from a free-text opportunity title.
- `write_artifacts(workspace_root, slug, files)` — creates the
  `<workspace>/automations/<slug>/` directory and writes each
  artifact file atomically. Returns the absolute paths.
- `make_recipe_docx(output_path, slug, recipe_data)` — convenience
  wrapper around `brief_writer.make_brief()` with `brief_kind="automation_recipe"`
  for the user-facing setup recipe deliverable.

The skill prompt composes the artifact CONTENT (zap-config JSON, Python
skeleton, n8n flow JSON, etc.) — this helper handles the filesystem
side: slug derivation, directory creation, atomic writes, path
resolution.

Companion to `skills/scaffold-automation/SKILL.md`. The skill SHOULD
go through this helper rather than hand-rolling file writes — same
discipline as `brief_writer.py` / `people_writer.py` / `atomic_write.py`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional


_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    """Turn a free-text opportunity title into a kebab-case slug.

    Lowercases, replaces every run of non-alphanumeric chars with a single
    hyphen, strips leading/trailing hyphens. Idempotent — running twice
    on the result gives the same output.

    Examples:
        "QuickBooks Estimate → Sheets pipeline"  → "quickbooks-estimate-sheets-pipeline"
        "Auto-categorize Gmail receipts"          → "auto-categorize-gmail-receipts"
        "    "                                    → "" (caller should reject)
    """
    if not isinstance(title, str):
        raise TypeError(f"slugify expects str, got {type(title).__name__}")
    cleaned = _SLUG_CLEAN_RE.sub("-", title.lower()).strip("-")
    return cleaned


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text to `path` atomically: write to temp file in same dir, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_artifacts(
    workspace_root: str,
    slug: str,
    files: Dict[str, str],
) -> Dict[str, str]:
    """Write a bundle of artifact files for a scaffolded automation.

    Creates `<workspace_root>/automations/<slug>/` if it doesn't exist
    and writes each file atomically. If the slug directory already
    contains conflicting files, raises FileExistsError BEFORE writing
    anything — no partial scaffolds.

    Args:
        workspace_root: absolute path to the workspace (where `_hq/`
            lives, NOT inside `_hq/`). Per Rule 25 path resolution.
        slug: kebab-case slug from `slugify()` or an explicit value.
        files: dict mapping filename → content. Filenames are relative
            to the slug directory (no path separators allowed).

    Returns:
        dict mapping filename → absolute path written.

    Raises:
        ValueError on bad inputs (empty slug, filename with separator).
        FileExistsError if any target file already exists.
    """
    if not slug:
        raise ValueError("slug must be non-empty")
    if not isinstance(files, dict) or not files:
        raise ValueError("files must be a non-empty dict")

    target_dir = Path(workspace_root) / "automations" / slug

    abs_paths: Dict[str, str] = {}
    # Pre-flight: validate filenames + check for conflicts BEFORE creating
    # the directory. Otherwise an abort here would leak an empty
    # `automations/<slug>/` on disk.
    for filename in files:
        if "/" in filename or "\\" in filename:
            raise ValueError(f"filename must not contain path separators: {filename!r}")
        target = target_dir / filename
        if target.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing artifact at {target}. "
                "Pick a new slug or remove the existing scaffold first."
            )
        abs_paths[filename] = str(target.resolve())

    # Pre-flight passed — create directory and write all files (atomic per file).
    target_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        _atomic_write_text(target_dir / filename, content)

    return abs_paths


def make_recipe_docx(
    output_path: str,
    *,
    title: str,
    subtitle: str,
    steps: list[str],
    rollback_steps: list[str],
    estimated_time_saved_minutes_per_week: Optional[int] = None,
) -> str:
    """Render the user-facing setup-recipe `.docx` via brief_writer.

    Convenience wrapper that composes the recipe sections in a consistent
    shape and routes through `brief_writer.make_brief()` with
    `brief_kind="automation_recipe"`. Per CONTRACT Rule 27 (no .md
    deliverables) the recipe is `.docx`.

    Args:
        output_path: absolute path for the .docx (typically
            `<workspace>/automations/<slug>/setup-recipe.docx`).
        title: human-readable automation name.
        subtitle: 1-line summary (e.g. "QuickBooks → Google Sheets").
        steps: ordered list of setup instructions, each ≤ 300 chars.
        rollback_steps: ordered list of undo instructions for if the
            automation breaks.
        estimated_time_saved_minutes_per_week: optional time savings
            estimate, surfaced in the recipe footer.

    Returns: `output_path` on success.
    """
    # Lazy import so this module doesn't pull python-docx for callers
    # that only need slugify / write_artifacts.
    from brief_writer import make_brief  # noqa: E402

    sections = [
        {
            "heading": "What this automation does",
            "body": subtitle,
        },
        {
            "heading": "Setup steps",
            "bullets": list(steps),
        },
        {
            "heading": "Rollback (if it breaks)",
            "bullets": list(rollback_steps),
        },
    ]

    if estimated_time_saved_minutes_per_week is not None:
        per_year_hours = round(
            estimated_time_saved_minutes_per_week * 52 / 60, 1
        )
        sections.append(
            {
                "heading": "Estimated time saved",
                "body": (
                    f"{estimated_time_saved_minutes_per_week} minutes/week "
                    f"≈ {per_year_hours} hours/year"
                ),
            }
        )

    return make_brief(
        output_path,
        brief_kind="automation_recipe",
        title=title,
        subtitle=subtitle,
        sections=sections,
        footer_text="Command Room — automation recipe",
    )


def main_cli(argv: list[str]) -> int:
    """CLI dispatcher for orchestrator bash invocations.

    Usage:
        python scaffold_automation.py slugify "<title>"
        python scaffold_automation.py write '<json_payload>'
            where json_payload = {"workspace_root": "...", "slug": "...",
                                  "files": {"name": "content", ...}}
    """
    if len(argv) < 2:
        print("Usage: scaffold_automation.py {slugify|write} <arg>", file=sys.stderr)
        return 2

    cmd = argv[1]
    if cmd == "slugify":
        if len(argv) < 3:
            print("slugify needs a title argument", file=sys.stderr)
            return 2
        print(slugify(argv[2]))
        return 0
    elif cmd == "write":
        if len(argv) < 3:
            print("write needs a JSON payload argument", file=sys.stderr)
            return 2
        payload = json.loads(argv[2])
        paths = write_artifacts(
            payload["workspace_root"],
            payload["slug"],
            payload["files"],
        )
        print(json.dumps(paths, indent=2))
        return 0
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2


__all__ = ["slugify", "write_artifacts", "make_recipe_docx"]


if __name__ == "__main__":
    sys.exit(main_cli(sys.argv))
