#!/usr/bin/env python3
"""FB-plumbing item 2 — the deliverable sweep excludes engineering docs.

The sweep walks the whole workspace for hand-rolled deliverables and voice/leak-
scans them. But the system's OWN engineering docs — coordinator handoffs, big-
test runbooks, build reports, review notes, substrate notes — legitimately quote
real org names and substrate tokens inside fenced blocks. They are docs ABOUT the
system, not client deliverables, and scanning them is pure false-positive noise
(a BIG_TEST_RUNBOOK is exactly the doc that surfaced this).

This pins the exclusion two ways:
  - a planted `handoffs/` doc (and RUNBOOK / HANDOFF_ / REVIEW_ / BUILD_REPORT_ /
    SUBSTRATE_ named docs) carrying a fenced substrate token is EXCLUDED — never
    a candidate, never scanned, never flagged;
  - the SAME token in a real deliverable path IS flagged.

G14: the one fixture file's mtime is "now" (written this run). Placeholder names
only. House convention: non-zero exit = fail.
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
from pathlib import Path

# G14 — compute date-stamps at runtime (never a hardcoded today-or-future ISO
# literal). The dates here are cosmetic filename stamps, not clock inputs.
_DATE = dt.date.today().isoformat()
_YM = dt.date.today().strftime("%Y-%m")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import deliverable_sweep as ds  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))
    else:
        print(f"  ok   {label}")


# A guaranteed leak: an internal id token (`_FORBIDDEN_PATTERNS` internal_id).
# A real client deliverable must never contain it; an engineering doc quoting
# substrate shape routinely does — that's the whole point of the exclusion.
_FENCED = "Here is the shape:\n\n```\n{\"owner_id\": \"person_042\"}\n```\n"


def _plant(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    root = Path(tempfile.mkdtemp(prefix="sweep_infra_"))

    # --- engineering docs that MUST be excluded ----------------------------
    infra = {
        "handoffs dir": _plant(
            root, "Penelopes Brain/Command Room/handoffs/coordinator_note.md",
            _FENCED),
        "BUILD_REPORT_ prefix": _plant(
            root, "Penelopes Brain/BUILD_REPORT_fb_plumbing.md", _FENCED),
        "RUNBOOK contains": _plant(
            root, f"BIG_TEST_RUNBOOK_{_YM}.md", _FENCED),
        "HANDOFF_ prefix": _plant(
            root, "HANDOFF_pipe1_to_code.md", _FENCED),
        "REVIEW_ prefix": _plant(
            root, "REVIEW_cr_obj1.md", _FENCED),
        "SUBSTRATE_ prefix": _plant(
            root, "SUBSTRATE_schema_notes.md", _FENCED),
    }

    # --- a REAL deliverable carrying the same token ------------------------
    deliverable = _plant(
        root, f"Penelopes Brain/Acme/deliverables/Acme_Memo_{_DATE}.md",
        _FENCED)

    # 1. candidate discovery: infra docs never become candidates -----------
    candidates = {str(p) for p in ds.find_candidate_text(root)}
    for label, p in infra.items():
        check(f"excluded from candidates: {label}", str(p) not in candidates,
              str(p))
    check("real deliverable IS a candidate", str(deliverable) in candidates,
          repr(sorted(candidates)))

    # 2. full sweep: the token flags in the deliverable, nowhere else ------
    result = ds.sweep_workspace(root, emit=False)
    flagged_paths = {f.get("path") for f in result["flagged"]}
    check("real deliverable is flagged", str(deliverable) in flagged_paths,
          repr(sorted(flagged_paths)))
    deliv = next((f for f in result["flagged"]
                  if f.get("path") == str(deliverable)), None)
    check("deliverable flag is a real violation (the fenced token leaked)",
          deliv is not None and deliv.get("has_violation") is True, repr(deliv))
    for label, p in infra.items():
        check(f"engineering doc never flagged: {label}",
              str(p) not in flagged_paths)

    # 3. the shared predicate directly (belt) ------------------------------
    check("_is_infra_path True for a handoffs doc",
          ds._is_infra_path(infra["handoffs dir"]))
    check("_is_infra_path True for a RUNBOOK anywhere in the name",
          ds._is_infra_path(Path("/x/BIG_TEST_RUNBOOK_2026-07.md")))
    check("_is_infra_path False for a normal client memo",
          ds._is_infra_path(deliverable) is False)

    print()
    if failures:
        print(f"FAIL — {len(failures)}/{checks} infra-exclusion checks failed")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print(f"OK — all {checks} infra-exclusion checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
