#!/usr/bin/env python3
"""Guard G2 — retired-token grep over skills/ + shared/.

Each token below was retired by a specific release; any new occurrence is
sediment reintroducing a dead pattern. Scoped allowances cover the places a
token is legitimately load-bearing (migration parsers reading OLD workspaces,
stale-marker registries that exist to DETECT the token, do-not-write lists,
one-line historical citations in CONTRACT/HISTORY).

Run: PYTHONUTF8=1 python tests/run_guard_g2_retired_tokens_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (regex, label, allowed_path_substrings)
# An occurrence is allowed if the file path contains ANY allowed substring.
TOKENS = [
    (r"_hq/DECISIONS\.md", "_hq/DECISIONS.md (retired — decision events + views/DECISION_LOG.md)", ["references/HISTORY.md"]),
    # bare DECISION_LOG is banned only in WRITE-shaped lines; reads of the
    # legacy path are legitimate in v1.x migration parsers + do-not-write lists.
    # Negative lookahead skips do-not-write lists ("You do not write to ...").
    # Also skips declared backward-compat view copies (the view regenerator
    # legitimately writes _hq/DECISION_LOG.md alongside _hq/views/).
    (r"(?i)(?<!not )(?<!never )(?![^\n]*backward-compat)\b(append|write|update|save)\b[^\n]*_hq/DECISION_LOG\.md", "write-shaped bare _hq/DECISION_LOG.md (writes go through decision events; the view regenerates)", ["workspace-ingest", "references/HISTORY.md"]),
    (r"BACKLOG\.md", "BACKLOG.md (never existed as a real tracker)", ["references/HISTORY.md"]),
    (r"computer:///[^\s)\]]*%", "URL-encoded computer:/// link (Windows native form since v3.13.0 — use get_brief_artifact_url)", ["shared/CONTRACT.md", "references/HISTORY.md", "brief_path"]),
    (r"plugin-source-v2", "plugin-source-v2/ (retired plugin dirname)", ["references/HISTORY.md"]),
    (r"commandroom1", "commandroom1 (retired marketplace remote)", ["canonical_edit_surface", "references/HISTORY.md", "command-room-update-bridge"]),  # update-bridge carries it as a STALE-MARKER so planted files re-flag (P0.8)
    (r"email_drafts/", "email_drafts/ folder (retired pre-v3.7.0 draft-file pattern)", ["shared/CONTRACT.md", "references/HISTORY.md", "MD_DELIVERABLE_POLICY.md"]),
    (r"\bOrgs Map\b", "Orgs Map (renamed Workspace Map v3.5.0)", ["enable-workspace-map", "enable-quick-commands", "command-room-update-bridge", "references/HISTORY.md"]),  # legacy-alias declarations + artifact id
]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    violations: list[str] = []
    for d in (ROOT / "skills", ROOT / "shared", ROOT / "references"):
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.md")):
            rel = p.relative_to(ROOT).as_posix()
            text = p.read_text(encoding="utf-8")
            for pat, label, allowed in TOKENS:
                if any(a in rel for a in allowed):
                    continue
                for m in re.finditer(pat, text):
                    line_no = text.count("\n", 0, m.start()) + 1
                    violations.append(f"{rel}:{line_no}: [{label}]")

    if violations:
        print(f"FAIL — {len(violations)} retired-token occurrence(s):\n")
        for v in violations:
            print(f"  ✗ {v}")
        print("\nEach token was retired by a named release — delete the sediment or, if genuinely load-bearing, extend the scoped allowance WITH justification.")
        return 1
    print("OK — no retired tokens outside their scoped allowances")
    return 0


if __name__ == "__main__":
    sys.exit(main())
