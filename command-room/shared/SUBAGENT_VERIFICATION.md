# Subagent Numeric-Verification Contract — v1 (Phase 5 / R6)

**Purpose:** a subagent's output is a *delegation, not a source of truth*. Any number a subagent reports MUST be re-derived through a canonical code helper before it reaches a rendered surface. Qualitative reasoning from a subagent is welcome; the *number* is always the code's.

**Read with:** `RELIABILITY.md`, `WORKSPACE_API.md`.

---

## Why this exists (proven necessary twice, 2026-07-01)

1. An Explore subagent reported commitment capture "decaying −70%, latest event Jun 24." The code-verified truth was **417 events since Jun 24** (23 on Jul 1). Root cause: it parsed one timestamp field while the substrate uses three (`ts` / `timestamp` / `date`) — the exact field drift `event_time.event_time()` exists to absorb.
2. A raw-storage inspection reported "show my schedule reports 1 of 11" tasks visible. The loader merged correctly; the subagent's hand-count was wrong.

Both were *confident* and *wrong*. A printed number a human can't check is not evidence — a re-derivation through the canonical helper is. This is the same enforcement model as Bug #98 (bind to the substrate artifact, not the narration), applied to fan-out orchestration.

---

## The rule

> Any orchestrator that fans out subagents — **boardroom** (parallel seat verdicts), **weekly-recap** (parallel connector reads + synthesis), the **session-sweep** extraction pass, and any future multi-agent skill — MUST, before rendering, re-verify every numeric claim through the canonical helper for that number. A subagent may say *"commitments are piling up on the sales side"*; it may **not** be the source of *"37 open."*

Concretely:
- A seat / sub-reader returns its qualitative read **plus** the raw ids/refs it based a count on — never a pre-computed total the renderer trusts.
- The orchestrator recomputes the total itself, in code, through the helper below, and renders **that**.
- If the subagent's number and the helper's number disagree, the helper wins and the discrepancy is worth a log line (it means a reader is drifting).

## Canonical helpers (the only sources of a rendered number)

| Number | Canonical helper |
|---|---|
| Open / overdue / undated commitment counts, by direction or kind | `commitment_state.commitment_counts(workspace_root)` (I/O) / `count_commitments(...)` (pure) — the ONE counting API |
| Delivered briefs / drafts / documents, conservative hours saved | `value_receipt.compute_value_receipt(workspace_root, start, end, ...)` — numbers computed in code, emitted as an audit event |
| An event's timestamp / recency ordering | `event_time.event_time(ev)` — reads `ts` → `timestamp` → `date` in priority order (the field drift behind failure #1) |
| Loading the event log (shard-transparent, defensive) | `events_io` / `event_refs.load_events(...)` |
| "Already captured?" dedup membership | `source_ref_index.check(...)` |
| Financials (P&L, cash, AR aging) | the QuickBooks MCP directly — never a seat's recollection of a figure |

A subagent that needs a count reads through the same helpers; it does not eyeball events.jsonl and total by hand (that produced failure #1).

## Enforcement

- **This document** is the canonical contract; fan-out orchestrators reference it in their SKILL.md.
- **Guard test** `tests/run_subagent_verification_gate_test.py`: this file exists and names the canonical helpers, and every fan-out orchestrator (boardroom, weekly-recap) references it — so a new number-rendering subagent surface can't ship without wiring the gate.
