#!/usr/bin/env python3
"""
Structural guard: every event type read by production code must have a producer,
and (softly) every event written should have a consumer.

WHY THIS EXISTS
---------------
Command Room is event-sourced via `_hq/data/events.jsonl`. A recurring, costly
bug class (tracked in `feedback_verify_consumers_before_ship.md`) is the
producer/consumer mismatch:

  * A reader filters for an event type that NO writer ever emits — the consumer
    waits forever. The feature looks shipped but is silently dead.
  * A near-miss in the type string (`project_proposed` written vs
    `project_proposal` read) — the two never meet.
  * A writer emits an event nobody reads — wasted write / dead feature.

These slip past unit tests because the tests feed the code the EXACT type name
the code looks for. The backfill no-op (v3.14.5) is the canonical example:
`source_event_seq_backfill` searched for `commitment_logged` /
`commitment_pending_review` / `commitment_captured`, but the real writer emits
plain `commitment`. Its unit tests passed because they wrote the phantom names;
in every real workspace the candidate pool was empty and the backfill did
nothing.

THE KEY DESIGN CHOICE
---------------------
The producer set and consumer set are built from PRODUCTION surfaces ONLY
(`skills/`, `shared/`, `references/`). `tests/` is deliberately EXCLUDED — a type
that only test fixtures write does NOT count as having a real producer. That is
exactly what makes this test catch the tests-green / prod-dead class that pure
unit tests structurally cannot.

WHAT IT CHECKS
--------------
1. DANGLING READS (hard fail): a canonical event type is read by production code
   but written by no production code. Either wire a writer or stop reading it.
2. NEAR-MISS TYPES (hard fail): a written/read token that is NOT canonical but
   sits within edit distance NEAR_MISS_MAX_DISTANCE of a canonical type
   (`project_proposed` vs `project_proposal`). Isolates the dangerous typo case
   without drowning in CR's overloaded non-event "type" values (widget type,
   brief type, entity type).
3. ORPHAN WRITES (soft / reported): a canonical type written by production code
   but read by no production code. Reported for review; allowlist the
   intentional audit-only ones in ORPHAN_WRITE_OK with a reason.

Known-intentional exceptions live in the *_OK allowlists below, each with a
one-line reason. A violation outside those lists fails the build.

Mirrors the house style of `run_no_retired_skills_test.py` /
`run_no_jargon_in_customer_surfaces_test.py`.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = PLUGIN_ROOT / "shared" / "data-schemas" / "events.schema.json"

# Production surfaces only — tests/ excluded on purpose (see module docstring).
SCAN_DIRS = ["skills", "shared", "references"]
SCAN_EXTENSIONS = {".md", ".py"}

# Files/dirs that legitimately NAME event types without being a real
# producer/consumer (narrative, schema, release log, this test).
EXEMPT_FILES = {
    "run_event_contract_test.py",
    "events.schema.json",
    "CHANGELOG.md",
}
# shared/releases/ = release manifests narrate event types in prose.
EXEMPT_DIR_PARTS = {("shared", "releases")}

# CR writes events in several forms, so writer-detection must be GENEROUS — a
# type read but written NOWHERE is the true bug; a type written via any of these
# forms is genuinely produced:
#   1. literal dict        {"type": "X"}                  (json blocks, py dicts)
#   2. variable assignment event_type = "X" if ... else "Y"
#   3. prose emission      write/emit/append/record a `X` event   (SKILL.md)
WRITE_LITERAL_RE = re.compile(r'["\']type["\']\s*:\s*["\']([a-z][a-z0-9_]+)["\']')
# the canonical event-type variable being assigned a string literal (note: `=`
# not `==`). Scoped to event_type/ev_type/evt_type ONLY — a broad `\w*type`
# match also catches widget fields like `input_type = "none"` (false positives),
# and event_type is the real producer idiom (e.g. skill_config_writer.py).
TYPE_ASSIGN_RE = re.compile(r'\b(?:event_type|ev_type|evt_type)\s*=\s*[^=]')
# prose: an emit/write verb on the line + a backtick-quoted token followed by "event"
PROSE_EMIT_VERB_RE = re.compile(
    r'\b(?:emit|emits|emitted|writ|append|record|records|stamp|log)\w*\b', re.IGNORECASE
)
PROSE_EVENT_TOKEN_RE = re.compile(r'`([a-z][a-z0-9_]+)`\s+event')

# --- Read detection ---
# (a) a type-comparison context line, OR (b) membership in a *_TYPES /
#     *_EVENTS constant collection (the backfill-bug pattern: a named tuple of
#     event types used as a filter, with no type-signal on the member lines).
TYPE_SIGNAL_RE = re.compile(
    r'\.get\(\s*["\']type["\']\s*\)'      # .get("type")
    r'|\[\s*["\']type["\']\s*\]'          # ["type"]
    r'|\bev_type\b|\bevent_type\b'        # ev_type / event_type vars
    r'|\bet\s*(?:==|in|!=)'               # et == / et in / et !=
    r'|\.get\(\s*["\']event["\']\s*\)'    # .get("event")
)
# Opens a multi-line type-constant collection, e.g. `COMMITMENT_TYPES = (`
TYPE_CONST_OPEN_RE = re.compile(r'\b[A-Z][A-Z0-9_]*(?:TYPES?|EVENTS?)\b\s*[:=].*[\(\[]')
QUOTED_TOKEN_RE = re.compile(r'["\']([a-z][a-z0-9_]+)["\']')
SNAKE_TOKEN_RE = re.compile(r'["\']([a-z][a-z0-9_]+)["\']')

# Tokens that show up quoted on type-signal lines but are field names / values,
# not event types — never treat these as reads.
NOT_EVENT_TOKENS = {
    "type", "event", "data", "ts", "timestamp", "id", "source_skill",
    "commitment_id", "thread_id", "target_id", "primary_thread_id",
    "open", "overdue", "complete", "status", "outcome", "kind",
    "role",  # transcript message field (gate2_turn_sweep reads it), not an event type
}

# -----------------------------------------------------------------------------
# Allowlists — known-intentional exceptions, each with a reason.
# -----------------------------------------------------------------------------

# Canonical types READ by production but with NO production writer that are
# nonetheless OK — e.g. emitted by an external system (Cowork runtime) or a
# legacy/defensive accept-list kept for old substrate.
DANGLING_READ_OK = {
    # commitment_superseded: RESOLVED (v4.6.0 C4) — the dead-path closer got
    # its writer: commitment_state.supersede_commitment (the merge verb). The
    # code-shaped type literal is detected, so no allowlist entry is needed.
    "person_added":"benign defensive accept — read in a 3-type OR filter by backfill_org_attribution; the other two (person_proposal, person_pending_review) ARE produced, so the backfill works; no add-path emits person_added today",
    "briefing": "value_receipt.compute_metrics reads `briefing` (SPEC C1 D4) to count briefs delivered, deduped against morning-brief pack_run by date; `briefing` is a canonical emit type per WORKSPACE_API.md but is written by scheduled-orchestrator/widget paths the code-shaped writer detector can't see — the read is a defensive secondary count behind the canonical pack_run signal",
    "memo_drafted": "value_receipt.compute_metrics reads `memo_drafted` (SPEC C1 D4 'documents produced') as one of the document event types; memo-writer documents emitting it (memo-writer/SKILL.md) but in a prose shape the emit-verb+backtick detector doesn't match, so it reads as writer-less here",
    "relationship_move_suggested": "REL1 — written by relationship_moves.compute_relationship_moves (rows built in a loop then atomic_append_jsonl'd, so the 'type literal next to the append' writer heuristic misses it); the read in relationship_moves._recently_excluded is the 7-day self-dedup check",
    "dont_forget_snooze": "canonical Pulse snooze event written via the dont-forget orchestrator's prose/widget path (no code-shaped writer); REL1's relationship_moves._recently_excluded reads it to honor Pulse snoozes in the weekly outreach dedupe",
}

# Canonical types WRITTEN by production with no production reader that are OK —
# audit-trail / telemetry / external-consumer-only events.
ORPHAN_WRITE_OK = {
    "pack_run": "audit/telemetry trail; aggregated on-demand by usage-report",
    "schedule_created": "audit trail of task registration; read by detectors",
    "visual_gate": "OUT2 §3 render-then-critique audit trail; written by visual_gate.log_visual_gate, mined on-demand by usage-report / insight-generator (prose-consumed, no code-shaped reader by design)",
}

# Near-miss tokens (close edit-distance to a canonical type) that are OK — e.g.
# legacy variants intentionally accepted defensively.
NEAR_MISS_OK = {
    "commitment_logged": "legacy alt-name accepted defensively by backfill candidate filter",
    "commitment_pending_review": "legacy alt-name accepted defensively by backfill candidate filter",
    "commitment_captured": "legacy alt-name accepted defensively by backfill candidate filter",
    # RESOLVED (v3.14.6): `org_proposal` (insight-generator's user_action event,
    # distinct from workspace-manager's `org_proposed` candidate event) was
    # added to events.schema.json enum per M's 2026-05-28 decision. It is now
    # canonical, so the near-miss loop skips it — no allowlist entry needed.
}

# Max edit distance at which a non-canonical type counts as a likely typo of a
# canonical one. 1-2 catches project_proposed↔project_proposal while ignoring
# unrelated non-event "type" values (button, email, org, call_prep, ...).
NEAR_MISS_MAX_DISTANCE = 2


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def load_canonical_types() -> set[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    enum = schema["properties"]["type"]["enum"]
    return {t for t in enum if isinstance(t, str)}


def _iter_scan_paths():
    for d in SCAN_DIRS:
        root = PLUGIN_ROOT / d
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in SCAN_EXTENSIONS:
                continue
            if "__pycache__" in path.parts:
                continue
            if path.name in EXEMPT_FILES:
                continue
            rel = path.relative_to(PLUGIN_ROOT).parts
            if any(
                len(rel) >= len(p) and rel[: len(p)] == p for p in EXEMPT_DIR_PARTS
            ):
                continue
            yield path


def scan():
    """Return (writes, reads) as dicts: type -> set of 'relpath:lineno'."""
    writes: dict[str, set[str]] = {}
    reads: dict[str, set[str]] = {}

    def add(bucket, tok, loc):
        if tok in NOT_EVENT_TOKENS:
            return
        bucket.setdefault(tok, set()).add(loc)

    for path in _iter_scan_paths():
        rel = path.relative_to(PLUGIN_ROOT)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        in_type_const = False
        for i, line in enumerate(text.splitlines(), start=1):
            loc = f"{rel}:{i}"
            is_type_assign = bool(TYPE_ASSIGN_RE.search(line))

            # --- WRITES ---
            for m in WRITE_LITERAL_RE.finditer(line):
                add(writes, m.group(1), loc)
            if is_type_assign:
                # event_type = "X" if ... else "Y" — every literal is a written type
                for m in SNAKE_TOKEN_RE.finditer(line):
                    add(writes, m.group(1), loc)
            if PROSE_EMIT_VERB_RE.search(line):
                for m in PROSE_EVENT_TOKEN_RE.finditer(line):
                    add(writes, m.group(1), loc)

            # --- READS ---
            # constant-collection of types (multi-line) — the backfill-bug shape
            if not in_type_const and TYPE_CONST_OPEN_RE.search(line):
                in_type_const = True
            if in_type_const:
                for m in SNAKE_TOKEN_RE.finditer(line):
                    add(reads, m.group(1), loc)
                if ")" in line or "]" in line:
                    in_type_const = False
            # type-comparison context (but NOT an assignment, which is a write)
            if TYPE_SIGNAL_RE.search(line) and not is_type_assign:
                for m in QUOTED_TOKEN_RE.finditer(line):
                    add(reads, m.group(1), loc)
    return writes, reads


def main() -> int:
    canonical = load_canonical_types()
    writes, reads = scan()
    write_types = set(writes)
    read_types = set(reads)

    # 1. Dangling reads: canonical, read by prod, no prod writer.
    dangling = {
        t for t in (read_types & canonical)
        if t not in write_types and t not in DANGLING_READ_OK
    }

    # 2. Near-miss types: a non-canonical token written or read whose edit
    #    distance to a canonical type is 1..NEAR_MISS_MAX_DISTANCE — almost
    #    certainly a typo'd producer/consumer (project_proposed↔project_proposal).
    #    Broad "phantom" matching was abandoned because "type" is overloaded in
    #    CR (widget type, brief type, entity type) and produced ~30 false hits.
    #    Near-miss isolates the dangerous case: a token that LOOKS like an event
    #    type and is one keystroke off a real one.
    near_miss = {}  # token -> (closest_canonical, distance)
    for t in (write_types | read_types):
        if t in canonical or t in NEAR_MISS_OK:
            continue
        best = None
        for c in canonical:
            d = _levenshtein(t, c)
            if 1 <= d <= NEAR_MISS_MAX_DISTANCE and (best is None or d < best[1]):
                best = (c, d)
        if best is not None:
            near_miss[t] = best

    # 3. Orphan writes (soft): canonical, written by prod, no prod reader.
    orphan = {
        t for t in (write_types & canonical)
        if t not in read_types and t not in ORPHAN_WRITE_OK
    }

    failed = False

    if dangling:
        failed = True
        print("FAIL — DANGLING READS (read by production, no production writer):")
        for t in sorted(dangling):
            print(f"  [{t}] read at:")
            for loc in sorted(reads[t])[:6]:
                print(f"      {loc}")
        print("  → wire a writer, fix the type string, or add to DANGLING_READ_OK with a reason.\n")

    if near_miss:
        failed = True
        print("FAIL — NEAR-MISS TYPES (look like a typo of a canonical event type):")
        for t in sorted(near_miss):
            closest, dist = near_miss[t]
            print(f"  [{t}] ≈ canonical [{closest}] (edit distance {dist}) at:")
            locs = sorted((writes.get(t, set()) | reads.get(t, set())))[:6]
            for loc in locs:
                print(f"      {loc}")
        print("  → fix the typo to the canonical spelling, or add to NEAR_MISS_OK with a reason.\n")

    show_orphans = "--orphans" in sys.argv
    if orphan and show_orphans:
        # Soft/informational. NOTE: this over-reports heavily — most CR consumers
        # read events via PROSE in SKILL.md ("aggregates X events"), which the
        # code-shaped read detector cannot see. Treat as an audit aid, not a gate.
        print("INFO — ORPHAN WRITES (written by prod; no *code-shaped* reader — many ARE read via prose):")
        for t in sorted(orphan):
            for loc in sorted(writes[t])[:4]:
                print(f"      {loc}  [{t}]")
        print("  → most are prose-consumed and fine; investigate only genuinely dead writes.\n")

    if failed:
        return 1
    print(
        f"OK — event contract holds. {len(canonical)} canonical types; "
        f"{len(write_types)} written, {len(read_types)} read in production; "
        f"no dangling reads, no near-miss types."
    )
    if orphan:
        print(
            f"   ({len(orphan)} canonical types have no code-shaped reader — "
            f"mostly prose-consumed; run with --orphans to list. Non-blocking.)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
