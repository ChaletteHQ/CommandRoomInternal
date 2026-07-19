#!/usr/bin/env python3
"""Guard G15 — remote-marketplace validity: committed paths + manifest/skill
description limits.

Why this guard exists (the July 2026 marketplace gotcha pair):

  * 2026-07-09 — an em-dash in a committed directory name broke marketplace
    install FLEET-WIDE (status=zip_invalid_path_characters). Every local test
    was green: the Claude Code CLI and the local filesystem are happy with
    non-ASCII paths. Only the Cowork REMOTE marketplace validator — which
    fetches the repo via the GitHub REST API and zips the plugin — rejects
    them, and only at SYNC time (install/update), i.e. at the customer.
  * Same class, same month — a "_comment" key in hooks.json shipped clean
    locally and failed remote validation fleet-wide. That one is enforced by
    run_guard_hooks_config_test.py. Between that guard and this one, EVERY
    known remote-marketplace validation rule now has a local ship-time mirror;
    if Anthropic's validator grows a new rule, add it to one of these two
    files so the marketplace rules stay findable in one place.

The rules enforced here (mirroring the server, not local tolerance):

  G15a  NON-ASCII PATHS — no byte > 0x7F in any committed path. The scan reads
        raw path bytes from `git ls-files -z` (the INDEX, not the checkout):
        the index is what GitHub serves the validator, and a Windows checkout
        can render a path differently than the bytes actually committed.
  G15b  MANIFEST DESCRIPTIONS — every `description` in .claude-plugin/
        plugin.json and every plugin entry's `description` in .claude-plugin/
        marketplace.json is <= 500 chars.
  G15c  SKILL DESCRIPTIONS — every committed SKILL.md frontmatter
        `description` is <= 1024 chars (the Agent Skills spec hard cap the
        validator enforces).
  G15d  NO MARKUP — no angle bracket (`<` or `>`) in ANY of the above
        descriptions; the validator rejects XML/HTML-shaped tags in
        description fields.

Relationship to G11 (run_guard_g11_description_budget_test.py): G11 is the
stricter LOCAL budget for command-room/skills descriptions (<= 980 chars,
routing-visibility rules, catalog total). G15 mirrors the SERVER's hard
reject and sweeps the WHOLE index — including manifests and any skill tree
outside command-room/skills that G11 does not read. Both stay: G11 can be
retuned per fork; G15 must always match Anthropic's validator.

Triage companion: cr-marketplace-500-gotcha — a sync 500 with NO validation
code is a GitHub outage, not a content failure; check githubstatus.com before
hunting for violations this guard would have caught.

Self-checks run first on synthetic fixtures (a scratch git repo with an
injected non-ASCII path, an over-cap manifest description, an over-cap
skill description, and an angle-bracket description) so a silently broken
scanner cannot report green.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

PLUGIN_MANIFEST_DESC_CAP = 500   # plugin.json / marketplace entry description
SKILL_DESC_CAP = 1024            # Agent Skills spec cap enforced remotely
ANGLE_RE = re.compile(r"[<>]")

try:
    import yaml
except ImportError:
    # Same convention as G11 / run_trigger_test.py: exit(0)-on-missing-dep
    # reads as PASS to run_all.py, and a guard that cannot guard must not
    # claim to have guarded.
    print(
        "ERROR: pyyaml required by the G15 marketplace-validity guard. "
        "Install with: pip install -r requirements.txt"
    )
    sys.exit(2)


def _git_index_paths(repo_root: Path) -> list[bytes]:
    """Raw path bytes of every committed/staged file, from the git INDEX."""
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git ls-files failed in {repo_root}: "
            f"{proc.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return [p for p in proc.stdout.split(b"\0") if p]


def check_path_bytes(raw: bytes) -> list[str]:
    """G15a — violations for one index path (empty list = clean)."""
    bad = sorted({b for b in raw if b > 0x7F})
    if not bad:
        return []
    shown = raw.decode("utf-8", errors="replace")
    hexes = ", ".join(f"0x{b:02X}" for b in bad)
    return [
        f"non-ASCII path in git index: '{shown}' (byte(s) {hexes}) — the "
        f"marketplace validator rejects the whole plugin "
        f"(zip_invalid_path_characters); git mv to an ASCII-only name"
    ]


def check_description(text: str, cap: int, where: str) -> list[str]:
    """G15b/c/d — violations for one description string."""
    v: list[str] = []
    if len(text) > cap:
        v.append(
            f"{where}: description is {len(text)} chars (marketplace cap "
            f"{cap}) — trim it; the validator rejects over-cap descriptions"
        )
    m = ANGLE_RE.search(text)
    if m:
        v.append(
            f"{where}: description contains '{m.group(0)}' — no angle-bracket/"
            f"XML tags in any description field; the validator rejects them"
        )
    return v


def _load_skill_description(path: Path) -> str | None:
    txt = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None  # unparseable frontmatter is another guard's problem
    if not isinstance(fm, dict):
        return None
    desc = fm.get("description")
    return desc if isinstance(desc, str) else None


def scan_repo(repo_root: Path) -> list[str]:
    """Run all G15 rules against one git repo. Returns violation strings."""
    violations: list[str] = []
    index_paths = _git_index_paths(repo_root)

    # G15a — path bytes straight from the index.
    for raw in index_paths:
        violations.extend(check_path_bytes(raw))

    # Content checks are driven by the SAME index list (only committed files
    # can reach the marketplace), read from the working tree.
    decoded = [p.decode("utf-8", errors="replace") for p in index_paths]

    for rel in decoded:
        f = repo_root / rel

        # G15b — plugin.json manifest description.
        if rel.endswith(".claude-plugin/plugin.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                violations.append(f"{rel}: not valid JSON ({e})")
                continue
            desc = data.get("description")
            if isinstance(desc, str):
                violations.extend(
                    check_description(desc, PLUGIN_MANIFEST_DESC_CAP, rel))

        # G15b — marketplace.json plugin-entry descriptions.
        elif rel.endswith(".claude-plugin/marketplace.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                violations.append(f"{rel}: not valid JSON ({e})")
                continue
            for i, entry in enumerate(data.get("plugins", []) or []):
                if not isinstance(entry, dict):
                    continue
                desc = entry.get("description")
                if isinstance(desc, str):
                    violations.extend(check_description(
                        desc, PLUGIN_MANIFEST_DESC_CAP,
                        f"{rel} plugins[{i}]"))

        # G15c — every committed SKILL.md frontmatter description.
        elif rel.endswith("SKILL.md"):
            if not f.is_file():
                continue  # index/worktree drift — battery runs pre-commit-clean
            desc = _load_skill_description(f)
            if desc is not None:
                violations.extend(
                    check_description(desc, SKILL_DESC_CAP, rel))

    return violations


# ---------------------------------------------------------------- self-checks

def _rmtree_force(tmp: Path) -> None:
    """rmtree that clears the read-only bit first: git object files are
    created -r--r--r--, and on Windows plain rmtree cannot delete them —
    with ignore_errors=True the whole scratch repo silently survives, so
    every battery fire leaked two fixture repos into temp."""
    def _clear_ro(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if sys.version_info >= (3, 12):
        shutil.rmtree(tmp, onexc=_clear_ro)
    else:
        shutil.rmtree(tmp, onerror=_clear_ro)


def _make_fixture_repo(tmp: Path, clean: bool) -> None:
    """A minimal committed plugin tree; clean=False injects all three
    violation classes (non-ASCII path, over-cap manifest desc + angle-bracket
    skill desc, over-cap skill desc)."""
    subprocess.run(["git", "init", "-q", str(tmp)],
                   capture_output=True, check=True, timeout=60)

    manifest_dir = tmp / ".claude-plugin"
    manifest_dir.mkdir()
    plugin_desc = "A well-behaved plugin." if clean else "x" * (
        PLUGIN_MANIFEST_DESC_CAP + 1)
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"name": "fixture", "description": plugin_desc}),
        encoding="utf-8")

    skill_a = tmp / "skills" / "alpha"
    skill_a.mkdir(parents=True)
    desc_a = ("Fires on 'run alpha'." if clean
              else "Renders <b>bold</b> output.")
    (skill_a / "SKILL.md").write_text(
        f"---\nname: alpha\ndescription: {desc_a}\n---\n\n# alpha\n",
        encoding="utf-8")

    skill_b = tmp / "skills" / "beta"
    skill_b.mkdir(parents=True)
    desc_b = "Fires on 'run beta'." if clean else "y" * (SKILL_DESC_CAP + 1)
    (skill_b / "SKILL.md").write_text(
        "---\nname: beta\ndescription: >-\n  " + desc_b + "\n---\n\n# beta\n",
        encoding="utf-8")

    if not clean:
        bad_dir = tmp / "docs — notes"   # the 2026-07-09 em-dash shape
        bad_dir.mkdir()
        (bad_dir / "readme.txt").write_text("x", encoding="utf-8")

    subprocess.run(["git", "-C", str(tmp), "add", "-A"],
                   capture_output=True, check=True, timeout=60)


def _selftest(failures: list[str]) -> None:
    def check(label: str, ok: bool) -> None:
        if not ok:
            failures.append(f"self-check failed: {label}")

    # Classifier units.
    check("ASCII path passes", check_path_bytes(b"skills/alpha/SKILL.md") == [])
    check("em-dash path flagged",
          bool(check_path_bytes("skills/t3 — fixes/x.md".encode("utf-8"))))
    check("at-cap manifest desc passes",
          check_description("x" * PLUGIN_MANIFEST_DESC_CAP,
                            PLUGIN_MANIFEST_DESC_CAP, "fx") == [])
    check("over-cap manifest desc flagged",
          bool(check_description("x" * (PLUGIN_MANIFEST_DESC_CAP + 1),
                                 PLUGIN_MANIFEST_DESC_CAP, "fx")))
    check("at-cap skill desc passes",
          check_description("y" * SKILL_DESC_CAP, SKILL_DESC_CAP, "fx") == [])
    check("over-cap skill desc flagged",
          bool(check_description("y" * (SKILL_DESC_CAP + 1),
                                 SKILL_DESC_CAP, "fx")))
    check("angle bracket flagged",
          bool(check_description("uses a <br> tag", SKILL_DESC_CAP, "fx")))

    # End-to-end on scratch git repos: inject all three classes on a copy,
    # the guard must go red on each; a clean copy must pass.
    for clean in (True, False):
        tmp = Path(tempfile.mkdtemp(prefix="g15_fixture_"))
        try:
            _make_fixture_repo(tmp, clean=clean)
            v = scan_repo(tmp)
            if clean:
                check("clean fixture repo passes", v == [])
            else:
                check("fixture non-ASCII index path goes red",
                      any("non-ASCII path" in s for s in v))
                check("fixture over-cap plugin.json desc goes red",
                      any("plugin.json" in s and "chars" in s for s in v))
                check("fixture over-cap SKILL.md desc goes red",
                      any("SKILL.md" in s and "chars" in s for s in v))
                check("fixture angle-bracket desc goes red",
                      any("angle-bracket" in s for s in v))
        finally:
            try:
                _rmtree_force(tmp)
            except OSError as e:
                failures.append(
                    f"self-check fixture cleanup failed: {tmp} ({e}) — "
                    f"scratch git repos must not accumulate in temp")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    failures: list[str] = []
    try:
        _selftest(failures)
    except Exception as e:
        print(f"ERROR: G15 self-checks could not run ({e}) — a guard that "
              f"cannot guard must not claim to have guarded.")
        return 2

    try:
        toplevel = subprocess.run(
            ["git", "-C", str(HERE), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=60)
        if toplevel.returncode != 0:
            raise RuntimeError(toplevel.stderr.strip())
        repo_root = Path(toplevel.stdout.strip()).resolve()

        # Vacuous-green defense: the scan must be looking at the index that
        # contains THIS guard (worktrees resolve via a .git file — a wrong or
        # empty index here would report clean forever).
        index_paths = set(_git_index_paths(repo_root))
        own_rel = Path(__file__).resolve().relative_to(
            repo_root).as_posix().encode("utf-8")
        if own_rel not in index_paths:
            raise RuntimeError(
                f"guard's own file {own_rel.decode()!r} is not in the "
                f"scanned git index at {repo_root} — wrong or empty index")

        failures.extend(scan_repo(repo_root))
    except Exception as e:
        print(f"ERROR: G15 marketplace-validity guard could not scan the git "
              f"index ({e}) — a guard that cannot guard must not claim to "
              f"have guarded.")
        return 2

    if failures:
        print("FAIL — remote-marketplace validity violation(s):")
        print()
        for msg in failures:
            print(f"  {msg}")
        print()
        print(f"Total: {len(failures)} violation(s)")
        print()
        print("These rules mirror the Cowork REMOTE marketplace validator,")
        print("which only fires at customer install/update time — a local")
        print("green with any of these present still breaks the fleet.")
        print("Companion guard: run_guard_hooks_config_test.py (hooks schema).")
        return 1

    print("OK — git index paths are ASCII-clean; all manifest and skill "
          "descriptions are within marketplace caps and markup-free")
    return 0


if __name__ == "__main__":
    sys.exit(main())
