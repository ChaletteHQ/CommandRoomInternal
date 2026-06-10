#!/usr/bin/env python3
"""Deterministic release-manifest selection for command-room-update-bridge
Phase 4.8 (v3.18.9+ — "solid for all clients" hardening).

WHY THIS EXISTS
---------------
The bridge has to pick WHICH per-version release manifests to play when a client
updates: every manifest with version `> last_applied AND <= current`, applied in
ascending version order. That selection was prose ("filter and sort by version")
left to the LLM at runtime — the same enforcement-gate failure class the rest of
this codebase keeps hitting. Version math is exactly where it bites:

  - A naive STRING sort/compare orders "3.10.0" BELOW "3.9.1" (because "1" < "9"
    at the third character). So a client on 3.9.1 updating to 3.18.9 would filter
    `v > "3.9.1"` and SILENTLY EXCLUDE every manifest from 3.10 through 3.18 —
    they'd miss every remediation across that range. Real clients exist on old
    versions (the retired commandroom2122–2177 per-version repos), so this is a
    live "not solid for all clients" hole, not a hypothetical.
  - Four-part versions (3.13.8.1) and any future double-digit minor/patch
    (3.18.10, 3.20.0) compound it.

This module does the selection deterministically: parse each version into a tuple
of ints and compare tuples. Pure, stdlib-only, no I/O beyond reading the releases
directory listing. The bridge SHELLS IN to this instead of doing version math in
prose — so the comparison can't drift per fire.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def parse_version(v: str) -> tuple[int, ...]:
    """'3.13.8.1' -> (3, 13, 8, 1). Raises ValueError on a non-numeric segment
    so a stray filename can't masquerade as a version."""
    return tuple(int(x) for x in str(v).strip().split("."))


def version_lt(a: str, b: str) -> bool:
    """True iff version a < version b (numeric, not lexical). Use for the
    bridge's `only_if: from_version < X` migration gates — a string compare
    wrongly skips a 2.9.0 client for a `< 2.10.3` gate ("2.9.0" > "2.10.3"
    lexically) and a 2.14.2 client for a `< 2.14.12` gate."""
    return parse_version(a) < parse_version(b)


def version_ge(a: str, b: str) -> bool:
    """True iff version a >= version b (numeric, not lexical)."""
    return parse_version(a) >= parse_version(b)


def version_in_range(v: str, last_applied: str, current: str) -> bool:
    """True iff parse(last_applied) < parse(v) <= parse(current).

    Tuple comparison gives the correct semantics for free: (3, 9, 1) < (3, 10, 0)
    (10 > 9 as ints), and a shorter tuple that is a prefix sorts BELOW the longer
    one — (3, 13, 8) < (3, 13, 8, 1) — which is exactly "3.13.8 came before its
    own .1 patch". Both the lexical-string bug and the 4-part bug vanish.
    """
    pv = parse_version(v)
    return parse_version(last_applied) < pv <= parse_version(current)


def select_pending_manifests(releases_dir, last_applied: str, current: str) -> list[dict]:
    """Return the manifests to play, ascending by version:
        [{"version": str, "path": str, "headline": str, "n_items": int}, ...]

    Selection: version strictly greater than `last_applied` (already applied —
    exclusive) and less-than-or-equal `current` (apply up to and including the
    version we just updated to — inclusive). `last_applied` may be "0.0.0" for a
    legacy/unknown install (every manifest plays). Files whose name isn't a clean
    `v<numeric.version>.json` are skipped, not guessed at.
    """
    releases_dir = Path(releases_dir)
    selected: list[tuple] = []
    for p in releases_dir.glob("v*.json"):
        name = p.name
        if not (name.startswith("v") and name.endswith(".json")):
            continue
        v = name[1:-5]
        try:
            pv = parse_version(v)
        except ValueError:
            continue  # not a version-shaped filename — ignore
        try:
            in_range = parse_version(last_applied) < pv <= parse_version(current)
        except ValueError:
            continue
        if not in_range:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        selected.append((pv, {
            "version": v,
            "path": str(p),
            "headline": data.get("headline", ""),
            "n_items": len(data.get("items") or []),
        }))
    selected.sort(key=lambda t: t[0])
    return [d for _pv, d in selected]


def _main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: release_remediation_selector.py <releases_dir> <last_applied> <current>",
              file=sys.stderr)
        return 2
    releases_dir, last_applied, current = argv[1], argv[2], argv[3]
    out = select_pending_manifests(releases_dir, last_applied, current)
    print(json.dumps(out, indent=2))
    return 0


__all__ = ["parse_version", "version_lt", "version_ge", "version_in_range",
           "select_pending_manifests"]


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
