#!/usr/bin/env python3
"""
SPEC OUT2 §2c — no stray palette constants (guard tier).

After OUT1/OUT2, colors and fonts for deliverable surfaces resolve through
`brand.get_brand()`; the component library (`shared/scripts/components.py`)
and the brand layer (`shared/scripts/brand.py`) are the only files allowed to
carry theme constants for those surfaces. This guard greps the whole plugin
for hex-color literals (`#RRGGBB`) plus python-docx `RGBColor(0x...)` literals
and FAILS on any hit outside the documented allowlist — so the next skill or
renderer edit can't quietly re-introduce a hardcoded theme (the pre-OUT1 drift
class this spec closes).

ALLOWLIST POLICY (every entry verified + documented below):
  - Entries are pinned with a max hit count. A count ABOVE the pin means a new
    color literal landed in an allowlisted file — that is a failure too; move
    the constant into brand.py / components.py (or update the pin in the SAME
    commit with a reviewed justification).
  - A count BELOW the pin passes (cleanup is progress, not breakage).

EXCLUDED FROM THE SCAN (not palette definitions):
  - tests/            — tests PIN theme values by design (they assert the
                        palette, they don't define one).
  - CHANGELOG.md      — historical release notes quoting shipped CSS values.
  - .git, __pycache__ — not source.

Run via: python3 tests/run_no_stray_palette_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # command-room/

HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")
RGBCOLOR_RE = re.compile(r"RGBColor\(0x")

SCAN_EXTENSIONS = {".py", ".md", ".html", ".js", ".json", ".css", ".svg", ".yaml", ".yml"}

EXCLUDED_PARTS = {".git", "__pycache__", "node_modules"}
EXCLUDED_TOP = {"tests"}          # tests pin theme values by design
EXCLUDED_FILES = {"CHANGELOG.md"}  # historical release notes

# path (posix, relative to command-room/) -> (max #RRGGBB hits, reason).
# Every entry verified 2026-07-10 (SPEC OUT2 session 1):
HEX_ALLOWLIST = {
    # THE theme owner. Palette values are stored as bare 6-hex (no '#') today,
    # so this usually scans at 0 — allowlisted so a future '#'-form example in
    # its docstring doesn't false-positive.
    "shared/scripts/brand.py": (12, "the brand layer — the ONE palette owner"),
    # The shared component library — HTML fragment backend. Colors resolve
    # through get_brand(); the single literal is the white-on-table_header
    # text contrast constant (same pairing the docx backend uses).
    "shared/scripts/components.py": (2, "component library — white-on-header contrast constant"),
    # Command Room PRODUCT widget chrome (dark Chalette theme: warm charcoal
    # surfaces, brass/gold accent) + the inline brand-logo SVG. This is
    # product UI, not a client deliverable surface — deliberately outside the
    # brand layer (client brand themes documents, never the product widget).
    # 92 hits verified as _WIDGET_CSS / _ALL_CLEAR_CSS / _ONBOARDING_SETUP_CSS
    # / _BRAND_LOGO_SVG values at pin time (139 total matches).
    "shared/scripts/chat_output_renderer.py": (139, "product widget chrome (dark theme) + logo SVG"),
    # Product-branded artifact templates (Chalette dark system) — same
    # product-chrome category as the widget CSS, self-contained by contract.
    "shared/templates/research_brief.html": (18, "research-brief artifact template (product dark theme)"),
    "skills/enable-quick-commands/references/quick-commands-artifact.html": (20, "quick-commands artifact template (product dark theme)"),
    "skills/enable-workspace-map/references/orgs-map-artifact.html": (13, "workspace-map artifact template (product dark theme)"),
}

# python-docx RGBColor(0x..) literals — .py files only.
RGBCOLOR_ALLOWLIST = {
    # The two white-on-navy table/matrix header text runs (contrast constant
    # paired with the brand table_header fill) — verified 2026-07-10. All
    # other docx colors resolve via brand.get_brand() -> _rgb().
    "shared/scripts/brief_writer.py": (2, "white-on-table_header text contrast constant (2 header renders)"),
}


def _iter_scan_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SCAN_EXTENSIONS:
            continue
        rel = path.relative_to(ROOT)
        parts = rel.parts
        if any(p in EXCLUDED_PARTS for p in parts):
            continue
        if parts[0] in EXCLUDED_TOP:
            continue
        if rel.as_posix() in EXCLUDED_FILES:
            continue
        yield path, rel.as_posix()


def main() -> int:
    failures: list[str] = []
    checked = 0

    for path, rel in _iter_scan_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        checked += 1

        hex_hits = HEX_RE.findall(text)
        if hex_hits:
            pin = HEX_ALLOWLIST.get(rel)
            if pin is None:
                lines = [
                    f"    line {i}: {ln.strip()[:100]}"
                    for i, ln in enumerate(text.splitlines(), 1)
                    if HEX_RE.search(ln)
                ][:5]
                failures.append(
                    f"{rel}: {len(hex_hits)} hex-color literal(s) in a non-allowlisted file — "
                    f"theme constants belong in shared/scripts/brand.py (palette) or "
                    f"shared/scripts/components.py (component backends). Resolve colors "
                    f"through brand.get_brand() instead.\n" + "\n".join(lines)
                )
            elif len(hex_hits) > pin[0]:
                failures.append(
                    f"{rel}: {len(hex_hits)} hex-color literals exceeds its allowlist pin "
                    f"of {pin[0]} ({pin[1]}) — a NEW color constant landed here. Move it "
                    f"into brand.py/components.py, or update the pin in the same commit "
                    f"with a reviewed justification."
                )

        if path.suffix == ".py":
            rgb_hits = RGBCOLOR_RE.findall(text)
            if rgb_hits:
                pin = RGBCOLOR_ALLOWLIST.get(rel)
                if rel == "shared/scripts/brand.py" or rel == "shared/scripts/components.py":
                    pin = pin or (99, "palette owners")
                if pin is None:
                    failures.append(
                        f"{rel}: {len(rgb_hits)} RGBColor(0x..) literal(s) in a "
                        f"non-allowlisted file — docx colors resolve through "
                        f"brand.get_brand() -> brief_writer._rgb()."
                    )
                elif len(rgb_hits) > pin[0]:
                    failures.append(
                        f"{rel}: {len(rgb_hits)} RGBColor(0x..) literals exceeds its "
                        f"allowlist pin of {pin[0]} ({pin[1]})."
                    )

    # The allowlist itself must not go stale: every entry must still exist.
    for rel in list(HEX_ALLOWLIST) + list(RGBCOLOR_ALLOWLIST):
        if not (ROOT / rel).is_file():
            failures.append(
                f"allowlist entry {rel} no longer exists — prune it from "
                f"tests/run_no_stray_palette_test.py."
            )

    print(f"stray-palette scan: {checked} files checked")
    if failures:
        print(f"\nFAIL — {len(failures)} stray-palette finding(s):\n")
        for f in failures:
            print(f"  - {f}\n")
        return 1
    print("OK — no stray palette constants outside the brand/components allowlist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
