# Entity-resolve + canonical-helper enforcement (v3.13.8+)

This document is the single source of truth for the cross-skill contract
that any skill resolving people / orgs / projects from loose input MUST
follow. It also covers the canonical-helper enforcement contract for
event-substrate reads (commitments, events.jsonl).

The recurring v3.13.x bug pattern this exists to close:

> Implementation exists in `shared/scripts/`. SKILL.md references it. The
> runtime path silently substitutes an inferior freelance read or grep.

Bugs #11 / #23 / #45 / #52 are all instances of this pattern. The fix
landed in v3.13.7 for 3 skills (workspace-manager, people-crm,
transcript-search) and is generalized to all entity-resolving skills in
v3.13.8.

## Entity resolution (mandatory before substring grep)

Before resolving any person / org / project from loose input, every skill
MUST call:

```python
from entity_resolve import resolve_all
candidates = resolve_all(workspace_root, query)
```

`resolve_all()` runs a 3-tier resolver:

- **Tier 1 — exact alias hit.** Instant, deterministic. If a query matches a
  known alias exactly, return that record. No fuzzy follow-up needed.
- **Tier 2 — fuzzy match (≥ 0.85 confidence).** Handles typos, minor
  reorderings ("Sample Sam" vs "Sam Sample"), abbreviation patterns
  (`SS` for `Sam Sample`).
- **Tier 3 — phonetic / Soundex (≥ 0.65 confidence).** Catches "Smyth"
  vs "Smith" / cross-locale spelling variants.

If `resolve_all()` returns NO candidates, ONLY THEN may a skill fall back
to substring grep against entities.json — and that fallback MUST be flagged
to the user, not silently surfaced as a single result.

**Skipping `resolve_all()` and grep'ing directly is a contract violation
as of v3.13.8.** It produces the false-uniqueness pattern that lost the
multi-candidate disambiguation step in 5+ skills pre-v3.13.7.

## Multi-candidate disambiguation

When `resolve_all()` returns multiple candidates above its top tier's
threshold, the skill MUST surface a disambiguation widget (per the
`shared/scripts/render_path_router.py` ladder + `find_existing_person`'s
`MultipleCandidatesError` contract) rather than picking the first one.

Pre-v3.13.7 the silent-first-pick pattern lost real distinctions across
multiple skills. The v3.13.7 enforcement-gate work added MUST-language
gates to 3 skills; v3.13.8 generalizes the rule.

## Canonical-helper enforcement (events.jsonl + commitments)

For ANY surface that displays / counts / acts on open commitments, every
skill MUST call the canonical reader:

```python
from cru_match import load_open_commitments
open_evs = load_open_commitments(events_jsonl_path)
```

The pre-v3.13.8 freelance fallback pattern (manual closure-suppression
scan with `try/except` + `isinstance(dict)` filter inline in the skill)
**is DEPRECATED.** It produces silent data divergence when:

1. malformed lines exist (Sub-bug #14b — freelance scan crashes; canonical
   `load_open_commitments` per v3.13.8 returns the surviving events + a
   non-empty `skipped` list)
2. closure-suppression rules evolve (v3.11.4 added `data.target_id` as a
   defensive closer-id field; freelance scans missed it)
3. dual-shape event schemas (HIGH/MEDIUM/LOW string vs 0.0-1.0 float
   confidence)

If `load_open_commitments()` returns degraded data (via
`load_events_defensively()`'s `skipped` channel), the skill MUST surface
the gap to the user as a soft banner ("Your activity log has N incomplete
entries — recovery pending in next update."). Do NOT silently re-implement
the read with your own filter.

The substrate-hygiene defense layer in `cru_match.load_events_defensively`
already handles malformed lines correctly and surfaces skipped-count to
consumers. Trust the canonical helper; surface its skipped-line warnings
to the customer; do not freelance.

## Seq reservation (events.jsonl writers)

Every event writer MUST call the canonical helper:

```python
from next_seq import next_seq
seq = next_seq(events_jsonl_path)
```

Do NOT compute `tail+1` (tail line may have no seq) or `max(seqs)+1` (would
propagate nano-epoch artifacts). The helper handles both shapes correctly.

## Skills covered by this contract (v3.13.8 audit)

The following skills MUST contain a reference to this document at the top
of their SKILL.md OR the inline ENTITY_RESOLVE_PROTOCOL preamble. The
cleanup / pre-ship enforcement test
(`tests/run_entity_resolve_enforcement_test.py`) fails if a new skill that
does loose-input resolution lands without one of these markers.

- workspace-manager
- people-crm
- transcript-search
- thread-resurrection
- intro-broker
- follow-up-ritual
- calendar-writer
- email-writer
- dormant-customer-scan
- morning-briefing

Skills that only act on already-resolved IDs (apply-choices,
meeting-notes Step 9b, scheduled-task orchestrators after their
classifier phase) are exempt — they receive resolved IDs from the
caller and do not perform loose-input resolution themselves.

## Why this matters

A SKILL.md instruction is a soft hint to the runtime. A shared protocol
document plus a structural test is a hard floor. The v3.13.7 ship
established the pattern for 3 skills; v3.13.8 extends it to all 10.
