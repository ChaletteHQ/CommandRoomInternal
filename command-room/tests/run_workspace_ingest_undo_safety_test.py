#!/usr/bin/env python3
"""
Regression guard for P0.6 (Phase 4 trust patch, 2026-07-02): workspace-ingest
undo/rollback must be snapshot-first and log-driven — never attribute-keyed.

THE BUG CLASS THIS CATCHES
--------------------------
Three data-loss shapes shipped together through v4.4.0:
(a) Undo deleted entities.json/events.jsonl/aliases.json when
    `last_writer == "workspace-ingest"` — but the AUGMENT merge sets exactly
    that on the user's MERGED substrate, so undo-after-augment deleted their
    pre-existing data.
(b) Rollback pointed at `_archive/` — but `_archive/ingest_source_*` backs up
    the SOURCE folder, not the target; no pre-merge snapshot existed. (A later
    revision blanket-deleted `_hq/data/**`, which is the same loss in AUGMENT.)
(c) Idempotency said re-running on a workspace with entities.json "exits" —
    which forbids AUGMENT, the skill's stated primary use case.

WHAT THIS GUARD ASSERTS (textual invariants on the shipped spec)
----------------------------------------------------------------
1. Phase 3.9 snapshot of `_hq/data/` exists and rollback/undo point at it.
2. No undo/rollback step keys a delete on `last_writer`.
3. Undo is log-driven: "only what the undo log records" language present,
   plus the run_start header + events_append seq-range shapes.
4. Idempotency: existing entities.json routes to AUGMENT, not exit.
5. Rollback no longer blanket-deletes `_hq/data/**`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SKILL = PLUGIN_ROOT / "skills" / "workspace-ingest" / "SKILL.md"

failures = []
checks = 0


def check(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


text = SKILL.read_text(encoding="utf-8")

# 1. Snapshot-first
check("Phase 3.9" in text, "Phase 3.9 snapshot phase missing")
check("pre-ingest-data_" in text, "pre-ingest snapshot path missing")
check(
    "restore `_hq/data/` from the Phase 3.9 snapshot" in text
    or "restore `_hq/data/` from the Phase 3.9" in text,
    "Rollback does not restore from the Phase 3.9 snapshot",
)

# 2. No attribute-keyed deletes anywhere in the file
check(
    re.search(r"[Dd]elete[^.\n]*last_writer\s*==", text) is None,
    'an undo/rollback step still keys a delete on `last_writer ==` — the P0.6(a) bug shape',
)

# 3. Log-driven undo
check(
    "only what the undo log records" in text.lower()
    or "ONLY what the undo log records" in text,
    "Undo is not scoped to what the undo log records as created",
)
check('"action":"run_start"' in text, "run_start undo-log header shape missing")
check('"action":"events_append"' in text, "events_append seq-range log shape missing")

# 4. Idempotency permits AUGMENT
check(
    "AUGMENT is the normal path" in text,
    "Idempotency section does not route existing-entities.json re-runs to AUGMENT",
)
check(
    "reset first — otherwise we're good." not in text,
    "Idempotency still carries the exit-without-writing reply that forbids AUGMENT",
)

# 5. No blanket data deletes in rollback
check(
    "Delete any partially-written `_hq/data/**`" not in text,
    "Rollback still blanket-deletes `_hq/data/**` — destroys merged substrate in AUGMENT",
)

# 6. Additive-history guard: undo must not rewrite events.jsonl past the range
check(
    "current max seq == logged `last_seq`" in text,
    "AUGMENT undo lacks the newer-events guard before stripping the seq range",
)

if failures:
    print(f"FAIL {len(failures)} of {checks} checks:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print(f"OK {checks} tests passed")
