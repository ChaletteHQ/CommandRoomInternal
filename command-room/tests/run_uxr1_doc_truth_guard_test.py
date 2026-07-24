#!/usr/bin/env python3
"""UXR1 D6 — doc-truth guard: no orchestrator/skill text may INSTRUCT a
banned display label or the banned section title again.

The two live defects this guard generalizes (both reviewer-classified,
fixed in UXR1):
  (i)  orchestrator-dont-forget.md instructed `Display label: `Resolved``
       — a LEGACY_DISPLAY_LABELS entry, banned on new renders since F-59.
       An orchestrator instructing a banned label re-creates the exact
       two-names-for-one-event disease the taxonomy killed.
  (ii) orchestrator-commitments.md's manual-assembly example emitted a
       section literally titled "↙ WAITING ON" — a title its own Naming
       section (CTS1 §4.1) bans inside the Waiting On chat.

Scope: every .md under skills/ and shared/ (the instruction layer — the
text a fired orchestrator executes). Pattern (i) targets the instruction
shape `Display label: `<banned>`` so history notes that merely MENTION an
old label ("display was 'Resolved'") stay legal; pattern (ii) targets the
assembly shape `"title": "…WAITING ON"` so prose explaining the ban stays
legal.

House convention: non-zero exit = fail.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from verb_taxonomy import LEGACY_DISPLAY_LABELS  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label)
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def instruction_docs() -> list[Path]:
    docs = list((ROOT / "skills").rglob("*.md"))
    docs += list((ROOT / "shared").rglob("*.md"))
    return docs


def main() -> int:
    docs = instruction_docs()
    check("instruction layer found (skills/ + shared/ markdown)",
          len(docs) > 20, f"only {len(docs)} docs")

    # (i) No doc may INSTRUCT a banned display label. The instruction shape
    # is `Display label: `X`` — the phrasing an orchestrator executes when
    # composing a row. Historical mentions without that shape stay legal.
    label_alt = "|".join(re.escape(lb) for lb in sorted(LEGACY_DISPLAY_LABELS))
    banned_label_re = re.compile(
        r"Display label:\s*`(" + label_alt + r")`")
    offenders = []
    for doc in docs:
        text = doc.read_text(encoding="utf-8", errors="replace")
        for m in banned_label_re.finditer(text):
            offenders.append(f"{doc.relative_to(ROOT)} instructs "
                             f"display label {m.group(1)!r}")
    check("no orchestrator instructs a LEGACY_DISPLAY_LABELS label",
          not offenders, "; ".join(offenders[:5]))

    # (ii) No doc may instruct assembling a section titled "WAITING ON"
    # (with or without the ↙ arrow) — banned inside the Waiting On chat by
    # orchestrator-commitments' own Naming section (CTS1 §4.1). The guard
    # keys on the assembly shape `"title": "…WAITING ON…"`, not prose.
    banned_title_re = re.compile(
        r'"title"\s*:\s*"[^"\n]*WAITING ON[^"\n]*"')
    offenders = []
    for doc in docs:
        text = doc.read_text(encoding="utf-8", errors="replace")
        for m in banned_title_re.finditer(text):
            offenders.append(f"{doc.relative_to(ROOT)}: {m.group(0)!r}")
    check('no orchestrator assembles a section titled "WAITING ON"',
          not offenders, "; ".join(offenders[:5]))

    print(f"{checks - len(failures)}/{checks} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
