# Source of Truth — Read/Write Convergence Contract (v3.11.4)

**Version:** 1.0 (2026-05-20)
**Companion to:** [`DATA_CONTRACT.md`](DATA_CONTRACT.md), [`VIEW_GENERATION.md`](VIEW_GENERATION.md), [`../shared/COMMITMENT_SCHEMA.md`](../shared/COMMITMENT_SCHEMA.md), [`../shared/PASSIVE_CAPTURE.md`](../shared/PASSIVE_CAPTURE.md)
**Why this exists:** The v3.11.3 morning-brief bug-bundle (B1-B4) and the source-of-truth audit that followed surfaced the same root cause four times: **writers emit one shape, readers look for another, projections are read as if they were authoritative, no structural defense enforces convergence**. This doc codifies the one architectural invariant that prevents that drift class from recurring.

---

## Rule 0 — The invariant

> **Every fact about the workspace lives in exactly two files: `_hq/data/entities.json` and `_hq/data/events.jsonl`. Everything else is either a derived projection (downstream, possibly stale) or a connector view (external, must be normalized into events.jsonl before driving any surface decision).**

This is not aspirational. It is the literal architectural contract every skill, orchestrator, view-generator, and consumer must obey. Violations cause user-visible bugs that look like "the dashboard says X but the timeline says Y" — the v3.11.3 bug-bundle was four separate violations.

---

## The three tiers

```
                  ┌─────────────────────────────────┐
                  │  TIER 1 — CANONICAL SOURCE      │
                  │  _hq/data/entities.json         │
                  │  _hq/data/events.jsonl          │
                  │  _hq/data/aliases.json          │
                  │  (writes go here, reads start   │
                  │   here for any decision)        │
                  └─────────────────────────────────┘
                       ▲                       │
       (overlay        │                       │ (project)
        derives-from)  │                       ▼
                  ┌─────────────────────────────────┐
                  │  TIER 2 — DERIVED VIEWS         │
                  │  _hq/views/MASTER_TRACKER.md    │
                  │  _hq/views/PEOPLE.md            │
                  │  _hq/views/DECISION_LOG.md      │
                  │  _hq/views/*.md                 │
                  │  (read for orientation; NEVER   │
                  │   for surface decisions without │
                  │   overlaying Tier 1)            │
                  └─────────────────────────────────┘
                                  ▲
                                  │ (must normalize through
                                  │  PASSIVE_CAPTURE before
                                  │  driving any surface)
                  ┌─────────────────────────────────┐
                  │  TIER 3 — CONNECTOR VIEWS       │
                  │  Gmail, Calendar, Slack, etc.   │
                  │  (external state; never trusted │
                  │   to be in workspace TZ or to   │
                  │   reflect "what's outstanding") │
                  └─────────────────────────────────┘
```

### Tier 1 — Canonical source

The only files where workspace state lives:

| File | What it holds | Writer pattern |
|---|---|---|
| `_hq/data/entities.json` | people, threads, orgs, workspace block | atomic-write via [`shared/scripts/atomic_write.py`](../shared/scripts/atomic_write.py); single-writer per `last_writer` field |
| `_hq/data/events.jsonl` | append-only event log (meetings, commitments, decisions, interactions, closures) | `atomic_append_jsonl`; never rewrite or reorder lines; corrections via `reclassification` event with `supersedes_seq` |
| `_hq/data/aliases.json` | raw-name → canonical-id mappings | atomic-write |

**All decision-driving reads start here.** No exceptions. If a skill needs to answer "what commitments are open?", "when was this thread last touched?", "has the user responded to this thread?", "is this decision still standing?" — the answer derives from Tier 1.

### Tier 2 — Derived views (projections)

Views under `_hq/views/` and the v1.8 mirror paths (`_hq/MASTER_TRACKER.md`, `_hq/PEOPLE.md`, etc.) are **outputs**, not inputs. They are regenerated when Tier 1 changes, but:

1. **Regeneration can lag.** A workspace whose last regen was 10 days ago will surface 10-day-old "Last touched" dates for threads with events today. The 2026-05-20 morning-brief bug was this exact case.
2. **Regeneration is not transactional.** A skill that writes 3 events and then re-renders views is not atomic — a consumer reading mid-write can see inconsistent state.
3. **Reading a view is fine for orientation.** Reading a view to *drive a surface decision* (what to show, what to count, what's overdue, what's stale) is **drift waiting to happen**.

**The overlay rule (REQUIRED for any skill that reads a Tier 2 view to drive a decision):**

```
1. Read the view's <!-- generated-at: YYYY-MM-DD HH:MM --> stamp.
2. If the stamp is older than 24h, the view is stale-by-default.
3. For every thread/person/decision the surface will render, scan events.jsonl
   for events newer than the view's stamp, scoped to that entity's id.
4. Override the view's value with the freshened value from events.jsonl.
5. Do NOT regenerate the view as a side effect — that's the writer's job
   (workspace-manager, cleanup). The overlay is read-only.
```

`morning-briefing/SKILL.md` Step 3a is the canonical implementation of this rule. Every Tier-2-reading skill follows the same shape.

### Tier 3 — Connector views (external)

Gmail, Calendar, Slack, Granola, etc. are external state. They are **never** in workspace TZ, never reflect "the user already replied" reliably (Gmail's `in:inbox` query excludes Sent — see B2 of v3.11.3), and never accept events.jsonl as a source. Three rules:

1. **Every connector read emits to events.jsonl.** Per [`shared/PASSIVE_CAPTURE.md`](../shared/PASSIVE_CAPTURE.md), reads produce writes. The act of reading on the user's behalf is the authorization to persist the canonical event.
2. **Every connector timestamp normalizes through [`shared/scripts/tz.py`](../shared/scripts/tz.py) `to_local(value, workspace_path=<WORKSPACE>)`.** Pass `workspace_path` explicitly (v3.11.3+ contract). UTC fallback is no longer silent — raises `TZResolutionError`.
3. **Connector views never drive "is this outstanding?" alone.** Gmail showing a thread in inbox is signal, not truth. The truth is: "Are there any inbound interaction events for this thread since the last outbound event from the primary user?" That's an events.jsonl query.

---

## The closure-convergence rule

**The bug class this prevents:** writer A emits a closure event with shape `{type: thread_resolved, data: {target_id: X}}`; reader B looks for `{type: commitment_resolved, data: {commitment_id: X}}`; the closure is silently invisible to reader B. show-my-list shipped this exact bug; v3.11.4 fixes it.

### Writer side — canonical close-event shapes

Every closure event MUST use one of these exact shapes:

| Event type | Required id field | Canonical writer helper |
|---|---|---|
| `commitment_resolved` | `data.commitment_id` | [`cru_match.build_commitment_resolved_event`](../shared/scripts/cru_match.py) |
| `thread_resolved` | `data.id` (preferred) OR `data.thread_id` | `log-resolution` SKILL.md |
| `decision_resolved` | `data.decision_id` (or `data.id`) | decision-log SKILL.md |
| `decision_superseded` | `data.supersedes_seq` (NOT a data.id field — points at original event seq) | decision-log SKILL.md |
| `commitment_review_dismissed` | `data.commitment_id` | [`cru_match.build_commitment_review_dismissed_event`](../shared/scripts/cru_match.py) |

**Forbidden field names for closer ids:**

- ~~`data.target_id`~~ — was used by show-my-list pre-v3.11.4; consumers don't read it. Renamed to `data.commitment_id` going forward. Defensive consumer-side acceptance preserved for in-flight events; new writes MUST use canonical.
- ~~`data.matter_id`~~ — pre-v2.7 legacy; the term "matter" was retired. Don't emit; readers don't accept.

If a writer is closing a commitment, it MUST use `commitment_resolved` with `data.commitment_id`. Writing `thread_resolved` with `data.target_id` and hoping a consumer picks it up is exactly the bug class this rule prevents.

### Reader side — canonical closer detection

Every consumer that counts open commitments / open threads / open decisions MUST go through the canonical helper:

- Commitments: [`cru_match.load_open_commitments(events_jsonl_path)`](../shared/scripts/cru_match.py). It handles all 5 commitment shape variants (canonical, flat-new, legacy, `owner_person_id`-variant, pending-review) AND treats both `commitment_resolved` and `thread_resolved` as valid closers (looking at `data.commitment_id`, `data.thread_id`, `data.id`, and as of v3.11.4 also `data.target_id` for historical events).
- Open review proposals: `cru_match.load_open_review_proposals(...)` — same closure-aware semantics.
- For other entity types (threads, decisions), the corresponding helper accepts every documented closure shape for that type.

**Direct `if ev["type"] == "commitment"` filtering without going through the helper is forbidden.** The 2026-05-17 commitment-shape-drift incident (Pulse dropping ~2/3 of commitments) and the 2026-05-20 show-my-list closure-shape incident were both direct-filter bypasses of the canonical helper.

---

## How to write a compliant skill

When in doubt, follow this checklist before merging any read or write surface:

**Reads:**

1. ☐ Does any decision the skill makes depend on a value from `_hq/views/*.md` or `_hq/MASTER_TRACKER.md` / `_hq/PEOPLE.md` / `_hq/DECISION_LOG.md`?
   - **Yes** → apply the Tier 2 overlay rule. Parse the view's `<!-- generated-at -->` stamp; for every entity the surface renders, scan events.jsonl for newer events and override.
   - **No** → continue.
2. ☐ Does the skill read a connector (Gmail, Calendar, Slack)?
   - **Yes** → (a) emit corresponding events to events.jsonl per PASSIVE_CAPTURE; (b) normalize timestamps through `tz.py to_local(value, workspace_path=<WORKSPACE>)`; (c) never treat the connector view as authoritative for "what's outstanding" — derive that from events.jsonl.
   - **No** → continue.
3. ☐ Does the skill count open commitments, threads, or decisions?
   - **Yes** → use `cru_match.load_open_commitments` (or the entity-type-appropriate canonical helper). No direct `ev["type"] == "commitment"` filters.

**Writes:**

4. ☐ Does the skill emit a closure event (commitment_resolved / thread_resolved / decision_resolved)?
   - **Yes** → use the canonical helper (`cru_match.build_commitment_resolved_event` etc.). Use the canonical id field (`data.commitment_id` for commitments, `data.id` or `data.thread_id` for threads). Never invent a new field name.
   - **No** → continue.
5. ☐ Does the skill emit any new event type, field name, or schema variant?
   - **Yes** → verify a consumer exists. Per [`feedback_verify_consumers_before_ship.md`](../../../../memory/feedback_verify_consumers_before_ship.md) — recurring bug class May 2026: "did I just ship a feature nobody reads?" If no consumer reads the new field, you have either dead code OR you're about to ship a bug. Resolve before merge.

---

## Structural defenses

[`tests/run_source_of_truth_test.py`](../tests/run_source_of_truth_test.py) enforces the contract:

- Scans every SKILL.md and orchestrator reference for "read MASTER_TRACKER" / "scan PEOPLE.md" / "read DECISION_LOG" patterns that drive decisions. Each must have an accompanying "overlay events.jsonl" / "scan events.jsonl for newer events" / `load_open_commitments` reference within 50 lines.
- Confirms every closure-event writer uses a canonical id field accepted by `cru_match.load_open_commitments`.
- Flags dead consumer references (events the codebase reads but no writer emits, OR events the codebase emits but no consumer reads).

This test is a release blocker. New skills that read a Tier 2 view without the overlay sibling clause fail CI.

---

## Related docs

- [`DATA_CONTRACT.md`](DATA_CONTRACT.md) — schema-level canonical-shape definitions (what entities and events look like on disk).
- [`VIEW_GENERATION.md`](VIEW_GENERATION.md) — how each Tier 2 view is regenerated from Tier 1.
- [`../shared/COMMITMENT_SCHEMA.md`](../shared/COMMITMENT_SCHEMA.md) — the 5-shape commitment-event reality, with `_commitment_field` as the canonical reader.
- [`../shared/PASSIVE_CAPTURE.md`](../shared/PASSIVE_CAPTURE.md) — the rule that every connector read produces an events.jsonl write.
- [`../shared/CONTRACT.md`](../shared/CONTRACT.md) — output-rendering rules (Rule 22 path resolution, Rule 26 no real customer names, etc.).
- [`../shared/RELIABILITY.md`](../shared/RELIABILITY.md) — skip-not-fail, OOO detection, missed-fire recovery, per-connector timeout budgets.

---

## History

- **v1.0 (2026-05-20)** — written in response to M's "things are not tied together correctly" call after the v3.11.3 morning-brief bug bundle. Codifies the 3-tier hierarchy, the overlay rule, the closure-convergence rule, and the structural defense.
