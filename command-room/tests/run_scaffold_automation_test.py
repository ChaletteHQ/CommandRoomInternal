#!/usr/bin/env python3
"""Tests for shared/scripts/scaffold_automation.py (v3.10.0+)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Make `shared/scripts` importable.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from scaffold_automation import slugify, write_artifacts  # noqa: E402


PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  OK  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


# ------------ slugify ------------

def test_slugify_basic() -> None:
    check("slugify lowercases", slugify("Hello World") == "hello-world")
    check("slugify collapses runs", slugify("foo   bar") == "foo-bar")
    check("slugify strips edges", slugify("--foo--") == "foo")
    check(
        "slugify drops unicode arrows",
        slugify("QuickBooks Estimate → Sheets") == "quickbooks-estimate-sheets",
    )
    check("slugify is idempotent", slugify(slugify("Hello World")) == "hello-world")
    check("slugify empty input returns empty", slugify("   ") == "")


def test_slugify_typeerror() -> None:
    raised = False
    try:
        slugify(123)  # type: ignore[arg-type]
    except TypeError:
        raised = True
    check("slugify raises on non-string", raised)


# ------------ write_artifacts ------------

def test_write_artifacts_basic() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        files = {
            "zap-config.json": '{"foo": "bar"}',
            "script.py": "# starter\n",
            "rollback.txt": "undo by deleting the zap\n",
        }
        paths = write_artifacts(tmp, "my-automation", files)
        check("returned all paths", len(paths) == 3)
        for filename, abs_path in paths.items():
            check(
                f"file {filename} exists",
                os.path.isfile(abs_path),
                detail=abs_path,
            )
            content = Path(abs_path).read_text(encoding="utf-8")
            check(
                f"file {filename} content matches",
                content == files[filename],
            )


def test_write_artifacts_rejects_existing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        slug_dir = Path(tmp) / "automations" / "my-auto"
        slug_dir.mkdir(parents=True)
        (slug_dir / "existing.json").write_text("preexisting", encoding="utf-8")

        raised = False
        try:
            write_artifacts(tmp, "my-auto", {"existing.json": "would overwrite"})
        except FileExistsError:
            raised = True
        check("write_artifacts refuses to overwrite", raised)

        # Critically: even on FileExistsError, no partial scaffold landed.
        # Test by passing a mix of conflicting + new files; verify NEITHER
        # was written.
        raised = False
        try:
            write_artifacts(
                tmp,
                "my-auto",
                {"existing.json": "x", "new-file.json": "y"},
            )
        except FileExistsError:
            raised = True
        check("write_artifacts refuses on partial conflict", raised)
        check(
            "no partial write on conflict (new-file.json was not created)",
            not (slug_dir / "new-file.json").exists(),
        )


def test_write_artifacts_rejects_bad_filename() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for bad in ("sub/dir/file.json", "..\\escape.json", "weird/path.txt"):
            raised = False
            try:
                write_artifacts(tmp, "ok-slug", {bad: "content"})
            except ValueError:
                raised = True
            check(f"rejects filename with separator: {bad!r}", raised)


def test_write_artifacts_rejects_empty_slug() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        raised = False
        try:
            write_artifacts(tmp, "", {"foo.txt": "x"})
        except ValueError:
            raised = True
        check("rejects empty slug", raised)


def test_write_artifacts_rejects_empty_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        raised = False
        try:
            write_artifacts(tmp, "ok-slug", {})
        except ValueError:
            raised = True
        check("rejects empty files dict", raised)


def main() -> int:
    test_slugify_basic()
    test_slugify_typeerror()
    test_write_artifacts_basic()
    test_write_artifacts_rejects_existing()
    test_write_artifacts_rejects_bad_filename()
    test_write_artifacts_rejects_empty_slug()
    test_write_artifacts_rejects_empty_files()

    print()
    print(f"=== {PASS} passed, {FAIL} failed ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
