#!/usr/bin/env python3
"""
Regression guard for P0.5 (Phase 4 trust patch, 2026-07-02): inbox-triage's
ball-in-court rule must resolve the user's address, never hard-code one.

THE BUG CLASS THIS CATCHES
--------------------------
The load-bearing direction-detection rule (Step 3.5, "Determining
ball-in-court from the latest message") hard-coded
`sender == matthew@chaletteholdings.com`. On any non-operator workspace the
clause silently never matched, degrading direction detection to labelIds only
— "the highest-severity single finding" of the 2026-07-01 plain-language
audit. The support-address exemption (CONTRACT Rule 26 / PRIVACY_POLICY) is
for report-bug's OUTBOUND draft target only — never for classification logic.

WHAT THIS GUARD ASSERTS
-----------------------
1. inbox-triage SKILL.md contains no matthew@ literal anywhere.
2. The ball-in-court rule names the canonical resolver
   (primary_user.resolve_primary_user).
3. The resolver actually resolves on a real-shape entities fixture (explicit
   pointer, legacy flag, and first-name fallback paths).
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "shared" / "scripts"))

from primary_user import resolve_primary_user_from_entities  # noqa: E402

SKILL = PLUGIN_ROOT / "skills" / "inbox-triage" / "SKILL.md"

failures = []
checks = 0


def check(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


text = SKILL.read_text(encoding="utf-8")

check(
    "matthew@" not in text,
    "inbox-triage SKILL.md hard-codes the operator's address — the P0.5 bug shape",
)
check(
    "resolve_primary_user" in text,
    "inbox-triage ball-in-court rule does not name primary_user.resolve_primary_user",
)

# Resolver behaves on real-shape entities across all three fallback paths.
ent_pointer = {
    "workspace": {"user_person_id": "person_sam", "user_first_name": "Sam"},
    "people": [{"id": "person_sam", "canonical_name": "Sam Sample"}],
}
check(
    resolve_primary_user_from_entities(ent_pointer) == "person_sam",
    "resolver failed on explicit workspace.user_person_id pointer",
)

ent_flag = {
    "workspace": {},
    "people": [
        {"id": "person_bo", "canonical_name": "Bo Sample"},
        {"id": "person_sam", "canonical_name": "Sam Sample", "is_primary_user": True},
    ],
}
check(
    resolve_primary_user_from_entities(ent_flag) == "person_sam",
    "resolver failed on legacy is_primary_user flag",
)

ent_name = {
    "workspace": {"user_first_name": "Sam"},
    "people": [
        {"id": "person_bo", "canonical_name": "Bo Sample"},
        {"id": "person_sam", "canonical_name": "Sam Sample"},
    ],
}
check(
    resolve_primary_user_from_entities(ent_name) == "person_sam",
    "resolver failed on user_first_name fallback",
)

if failures:
    print(f"FAIL {len(failures)} of {checks} checks:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print(f"OK {checks} tests passed")
