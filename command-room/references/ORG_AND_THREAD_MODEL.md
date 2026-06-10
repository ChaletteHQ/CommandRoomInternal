# Org & Thread Model (v2.10.3)

Canonical reference for how Command Room represents businesses, affiliations, threads of work, and the events that attach to them. Every skill that reads or writes the data layer conforms to this spec.

---

## Principles

1. **Hands-off for the CEO.** Classification and structure discovery are the system's job. The CEO is asked to confirm once during onboarding and once per week during the batched classifier review. Never mid-session.
2. **Connector-inferred, CEO-confirmed.** Org structure is proposed from real signals (email domains, Slack workspaces, calendar clusters, Drive folders, signatures). The CEO edits, doesn't invent.
3. **Graph, not tree.** Threads relate to orgs (tree), to each other (spawn, cross-ref), and to events (many-to-many). Rendering collapses to whatever view the CEO asked for.
4. **Confidence is a first-class field.** Every classification carries a score. Low confidence still ships captured data; uncertainty surfaces weekly, not per-event.
5. **No forced hierarchy.** The model works for solo CEOs (1 org), holding-co operators (N operating + client engagements), VCs (1 fund + portfolio), advisors (mostly advisory), family offices (investments + beneficiaries). Briefing layout is derived from what's present.

---

## Entities

### Org record

```jsonc
{
  "id": "org_042",
  "canonical_name": "Acme Co",
  "scope": "holding",                  // holding | operating | division | brand | fund | other
  "parent_org_id": null,               // null for top-level; org_id for nested
  "relationship_type": "client",       // see enum below
  "is_primary_focus": false,           // bubbles up in briefings when true
  "relationship_label": null,          // free-text override when relationship_type = "other"
  "aliases": ["CategoryCo", "Category"],
  "domains": ["category.example.com"],       // email/web domains associated with this org
  "slack_workspace_ids": ["T01ABC"],   // if tracked
  "inferred_from": ["email_domain_cluster", "slack_workspace"],
  "first_seen": "2026-01-04",
  "last_interaction": "2026-04-19",
  "status": "active"                   // active | archived
}
```

**`scope` enum** — what role this org plays in the tree:
- `holding` — top-level entity with operating children
- `operating` — a revenue-generating business unit
- `division` — a sub-unit inside an operating entity
- `brand` — customer-facing label that may span divisions
- `fund` — an investment vehicle (VC / PE / family office)
- `other` — escape hatch with `scope_label`

**`relationship_type` enum** — the CEO's role vs this org:
- `operating` — the CEO runs it
- `partner` — co-founded / co-owned
- `board` — board seat
- `advisory` — advisor / consultant
- `investment` — holds equity, does not run
- `client` — paid engagement (CEO is selling to / serving them)
- `portfolio_company` — portco of a fund the CEO runs
- `beneficiary` — family office beneficiary / grantor
- `vendor` *(v2.10.3)* — third party who sells the CEO products/services (one-off purchase, demo, contract)
- `prospect` *(v2.10.3)* — third party in active sales conversation, not yet a client
- `service_provider` *(v2.10.3)* — vendor with ongoing relationship: accountant, lawyer, agency, recurring service
- `other` — escape hatch with `relationship_label`

**`tier` enum** *(added v2.10.3)* — visual + filtering tier, independent of `relationship_type`:
- `primary` — the CEO's own org(s): holding + operating units they run. Renders prominent in Orgs Map. Surfaces in every daily flow.
- `secondary` — active client / partner / advisor / portfolio company. High-signal external relationships. Renders normally. Surfaces in daily flows.
- `external` — vendor / prospect / service_provider. Low-signal third parties. Renders smaller, under collapsed "External" section in Orgs Map. Only surfaces in daily flows when there's an open commitment or a meeting on the calendar.
- `passive` — dormant / archived / orphan. Historical reference only. Hidden from daily flows. Accessible by name.

Tier is set automatically at onboarding via the primary-affiliation gate + interaction-volume thresholds (see Discovery section below), or explicitly by the user via `make [Org] primary` / `make [Org] external` / etc.

**Tree rules:**
- `parent_org_id` must reference an existing org or be null.
- A child org inherits the primary focus of its root unless it sets `is_primary_focus` explicitly.
- Depth is unbounded; renderers collapse beyond a configurable threshold (default 3).
- Deleting a parent cascades to archiving children (not deletion).

### Thread record (formerly "project record")

Still stored as `project_NNN` ids for schema stability; user-facing language is "thread."

```jsonc
{
  "id": "project_104",
  "display_name": "Category — Restaurant sourcing bot",
  "folder_name": "Category_Restaurant_Sourcing",
  "kind": "initiative",                // see enum below
  "affiliation_id": "org_018",         // the operating co (Acme Restaurant), not the holding
  "parent_thread_id": null,            // nested threads
  "spawned_from_thread_id": null,      // lineage
  "cross_refs": [],                    // [{thread_id, reason}] soft links
  "stakeholder_person_ids": ["person_042", "person_055"],
  "owner_person_id": "person_033",
  "stage": "active",                   // active | dormant | paused | blocked | archived | exploring (v2.10.3+ adds dormant)
  "last_activity": "2026-04-18",
  "success_criteria": [],
  "status": "active"
}
```

**`kind` enum** (thread kinds): `initiative` · `deal` · `advisory` · `investment` · `board` · `relationship` · `theme` · `concern` · `ritual` · `personal` · `other`.

**Thread-to-org link**: `affiliation_id` points to the most specific org that owns the thread. For Sam's restaurant sourcing thread, that's `org_acme_restaurant`, not `org_acme_co` (the holding). Renderers roll up via the org tree.

**Thread graph**:
- `parent_thread_id` — nested threads (subproject replacement).
- `spawned_from_thread_id` — lineage when one thread birthed another.
- `cross_refs[]` — soft links for "this thread is related to that one" without hierarchy.

### Person record

Unchanged. `affiliation_ids[]` already supports multiple orgs.

---

## Events

### Event record

```jsonc
{
  "seq": 1042,
  "ts": "2026-04-19T14:33:00Z",
  "type": "meeting",                   // meeting | interaction | decision | commitment | ...
  "source_skill": "meeting-notes",
  "primary_thread_id": "project_104",
  "related_thread_ids": [
    "project_087",                     // Rio 1:1 (relationship)
    "project_092"                      // ABC Distributors vendor thread
  ],
  "cross_ref_reason": {
    "project_087": "attendee context",
    "project_092": "vendor mentioned in passing"
  },
  "person_ids": ["person_033", "person_042"],
  "classification_confidence": 0.82,
  "source_ref_hash": "sha256:...",
  "data": { /* type-specific payload */ }
}
```

**Multi-thread tagging rules:**
- Every event has exactly one `primary_thread_id` (where it lives in SESSION_NOTES).
- `related_thread_ids[]` may be empty or contain 1+ additional thread ids.
- `cross_ref_reason` maps each related thread to a short string explaining the link.
- Related threads render the event as a cross-ref entry in their view; only the primary owns the SESSION_NOTES append.

**`classification_confidence` bands:**
- `≥ 0.75` — auto-classify silently, high trust.
- `0.40–0.75` — auto-classify provisionally, surfaced in weekly classification review.
- `< 0.40` — auto-classify with best guess, flagged as `low_confidence` in weekly review.

The CEO is never prompted mid-session to resolve low confidence. Capture always succeeds.

---

## Discovery (onboarding)

`command-room-onboarding` Phase 2 runs automatically when connectors are available.

### v2.10.3 update — primary-affiliation gate + interaction-volume tiers

Discovery now happens in **three stages** instead of one. Each stage's output narrows what the next stage works with:

**Stage 1 — Identify the user themselves.** Read CEO's email signature blocks, primary email domain, Slack workspace ownership. Establish: the CEO's own primary affiliation (`is_primary_focus: true`, `tier: primary`). This becomes the anchor — every other org gets tiered RELATIVE to it, not as a peer.

**Stage 2 — Volume-tier all other orgs.** For each non-CEO-domain org found in connector signals, compute total interactions in onboarding window:

| Interactions / 30d | Default tier | Default relationship_type | Confidence |
|---|---|---|---|
| 1–5 | `external` | `vendor` (or `prospect` if outbound-heavy + sales-language) | low — needs Step 2b confirmation |
| 6–20 | `external` | `vendor` or `service_provider` if recurring monthly | medium — present in Step 2b for confirmation |
| 21–50 | `secondary` | `client` (most common) or `partner` if reciprocal | medium-high — present briefly in Step 2b |
| 51+ | `secondary` | `client` / `partner` / `advisor` based on signal pattern | high — auto-classify, surface as fact in Step 2b |
| 200+ AND on user's primary domain | `primary` | `operating` | high — auto-classify silently |

Volume-tier decisions are independent from confidence — a 50-interaction org can be high-confidence vendor (if all signals point to "they sold us a service") or low-confidence client (if signals are mixed).

**Stage 3 — Per-org explicit confirmation in Step 2b.** Render orgs grouped by tier with their inferred `relationship_type`. The user gets a one-key correction option per org:

```
Your orgs (primary):
  ✓ Acme Holdings (operating)
  ✓ Acme Restaurant (operating, owned by Acme Holdings)

Active engagements (secondary):
  1. Category Company (client) — confirm? `1 confirm` / `1 edit [partner|advisor|other]` / `1 remove`
  2. Northstar Partners (partner) — confirm? `2 confirm` / `2 edit ...` / `2 remove`

External (collapsed by default — say `show external` to expand):
  3. Chalette Holdings (vendor — 3 emails) — keep? `3 confirm` / `3 edit [client|advisor]` / `3 remove from workspace`
```

This is the gate that catches the "Chalette-as-Quinn's-org" leak — the user sees the inferred relationship_type explicitly and can correct it before commit.

### Standard signal sources (unchanged from v2.2)

1. **Gmail / Outlook**: cluster sender/recipient domains; extract domain counts.
2. **Slack / Teams**: list workspaces the CEO belongs to; membership implies affiliation.
3. **Calendar**: cluster recurring attendees into implicit teams; meeting titles hint at threads.
4. **Drive / OneDrive / SharePoint**: top-level folder names often mirror entity names.
5. **Signatures & footers in Gmail**: extract role + org titles per recipient.

2. **Propose tree**: the onboarding skill renders a best-guess tree — orgs with inferred `scope`, proposed `parent_org_id`, and proposed `relationship_type`. Example output to the CEO:

   ```
   I see 6 entities in your world:

   Acme Co (holding, client)
   ├── Acme Restaurant (operating)
   ├── Acme Catering (operating)
   ├── Acme Events (operating)
   └── Acme Wholesale (operating)
   [Holding Co] (operating, primary focus)
   NorthStar (client)
   BrightCo (operating, primary focus)
   Northstar Partners (partner)
   Acme Property (client)

   Does this look right? You can:
     - Confirm as-is
     - Adjust specific entries
     - Add missing ones
   ```

3. **CEO confirms once**. Edits land. Onboarding moves on.

Re-running discovery later is supported but rare: new connector = new signals = propose only what's new.

---

## Reclassification (rare, on-demand)

Reclassification exists for correctness but is **never proactively surfaced** outside weekly review.

**Capability:**
- Any event can be moved: `primary_thread_id` updated, `related_thread_ids[]` edited.
- A `reclassification` event is appended to events.jsonl with old + new values. History preserved.
- No silent mutation: the original event stays in place, the reclassification event is its amendment.

**Invocation:**
- CEO explicit: "that email about the vendor belongs under NorthStar, not Category" → reclassification event.
- Weekly classification review: batched multi-item confirm.
- Never: mid-session prompts for reclassification.

---

## Classifier feedback loop

`_hq/data/classifier_feedback.jsonl` accumulates every correction:

```jsonc
{"ts": "2026-04-20T18:00:00Z", "event_seq": 1042, "old_primary": "project_087", "new_primary": "project_104", "reason": "user_correction_weekly"}
```

The classifier reads this file on each run and weights future classifications. Over 4–6 weeks on one workspace, confidence on the CEO's specific vocabulary and org shape climbs; weekly review shrinks toward zero items.

---

## Migration from v1.8

`migration-v2` maps the legacy shape to this model:

| v1.8 concept | v2.2 destination |
|---|---|
| MASTER_TRACKER.md project row | Thread record with `kind: initiative` unless disambiguated |
| Client folder (peer to other projects) | Org record with `relationship_type: client` |
| PROJECT_CONTEXT.md | Untouched, stays as narrative artifact at thread folder root |
| Subproject (per SUBPROJECTS.md) | Thread with `parent_thread_id` set to the parent thread |
| home_org field (if present) | Org with `is_primary_focus: true`, `relationship_type: operating` |
| Decision log entry | Event `type: decision`, `primary_thread_id` = project, `classification_confidence: 1.0` |
| Single thread_id on events | `primary_thread_id` = that id, `related_thread_ids: []` |

---

## Skill ownership map (writers)

| File | Primary writer |
|---|---|
| `entities.json` — orgs | `command-room-onboarding`, `workspace-ingest` (folder-mode discovery, v2.14.20+); `workspace-manager` (updates) |
| `entities.json` — threads | `workspace-manager` (lifecycle); `meeting-notes` (last_activity updates) |
| `entities.json` — people | `people-crm`, `team-intelligence` |
| `events.jsonl` | All passive-capture-emitting skills |
| `classifier_feedback.jsonl` | `insight-generator` (weekly review writes), `workspace-manager` (explicit reclassification writes) |
| `aliases.json` | `workspace-manager`, `people-crm` |

Readers: everyone. Writers: only the owner per row above.

---

## What this replaces

- `subproject-graduation` skill — obsolete. Nested thread depth is native.
- `home_org_id` concept — replaced with `is_primary_focus` + `relationship_type`.
- Manual MASTER_TRACKER curation — MASTER_TRACKER.md becomes a regenerated view.
- Single-thread classification — replaced with primary + related multi-thread tagging.
- Mid-session reclassification prompts — replaced with silent auto-classify + weekly batched review.

---

## Lifecycle (v2.10.3+)

### Project state machine

```
exploring → active → dormant → archived
              ↑↓        ↑↓
           paused    blocked
```

**State transitions:**

- `exploring → active` — CEO promotes via `promote [name]` or via creation in `new project [Name]` (skipping the explore stage).
- `active ↔ paused` — explicit user action via `pause [project]` / `resume [project]`. Paused projects can be unblocked.
- `active ↔ blocked` — explicit user action via `block [project] reason: [text]` / `unblock [project]`. Different from paused — blocked has a stated reason.
- `active → dormant` — auto-transition. Don't Forget detects no activity in 30+ days → proposes one-key confirmation. 60+ days without action → auto-flips to dormant.
- `dormant → active` — auto-revive. `go [project]` + adding a session note flips back to active. Or explicit `revive [project]`.
- `dormant → archived` — auto-flip after 180 days dormant without revival.
- `archived → active` — explicit `revive [project]` only.

### Render-time filtering

Every daily-flow surface filters by `status`:

| Surface | Statuses surfaced |
|---|---|
| Upcoming Meetings | active, paused (if today's meeting is scheduled), blocked (if meeting is to unblock) |
| Inbox | all (mail surfaces independent of project status) |
| Commitments | active only (dormant + archived commitments hidden but accessible via `show all open`) |
| Don't Forget | active (for dormancy detection) + dormant proposal-confirmation prompts |
| Past Meetings | all (meeting processing happens regardless of project status) |
| `go [name]` | all — works for any status, dormant/archived projects load their cached substrate |
| Orgs Map | active prominent, dormant under collapsed "Dormant (N)" section, archived under collapsed "Archive (N)" section |

This means the v2.10.2 12-month historical backfill can auto-create projects for old activity without polluting the active workspace — those auto-created projects land as `dormant` (60+ days inactive) or `archived` (180+ days inactive) and stay invisible until the user opens them.

## Beacon — proactive entity surfacing (v2.10.3+)

Two passes in `insight-generator` now produce proposals for the user to confirm:

### Pass 9 — Project proposals (existing, unchanged shape)

Surfaces "this looks like a new project worth tracking" once a week. See insight-generator/SKILL.md for the four primary signals + stacking + cooldown logic.

### Pass 10 — Org proposals (new in v2.10.3)

Surfaces "this looks like a new org worth tracking" with inferred `relationship_type`. Same architecture as Pass 9:

**Primary signals (each can fire alone):**
1. **New email-domain cluster** — 5+ emails to/from a domain not in any existing org's `domains[]`. Weight: 5.
2. **New Slack workspace or Teams tenant** — CEO joined a workspace not tied to any known org. Weight: 5.
3. **New SharePoint site** — CEO got access to a site not under any known org. Weight: 4.
4. **Recurring counterparty in transcripts** — Granola transcripts mention an organization name 3+ times across different meetings, no matching org record. Weight: 4.

**Stacking signals (add weight to a fired primary):**
- Person from new domain attended 3+ meetings in 14 days: +2
- Drive doc shared with new domain: +1
- Signature block found with org name + title: +2
- Sales/contract language in messages with this org: +2 (signals prospect)
- Recurring billing/invoice signal: +2 (signals service_provider)

**Relationship_type inference from signal pattern:**
- Mostly inbound (you receive, rarely send) + generic email + 1-off purchase signals → `vendor`
- Mostly outbound (you send, they reply) + recurring meetings + sales language → `prospect`
- Mostly outbound (you send, they reply) + recurring meetings + paid engagement signals → `client`
- Reciprocal volume + senior-name signature + advisor language → `advisor` or `partner`
- Recurring billing + service description → `service_provider`

**Default tier:** all Pass 10 proposals default to `tier: external`. User can promote to `secondary` or `primary` via the confirmation action set.

**Surfacing cadence (the "both" decision per audit):**
- High-confidence proposals (≥3 strong signals) bubble up DAILY in Don't Forget alongside pending people-record reviews. Same `a/b/c confirm/edit/skip` action set.
- Lower-confidence proposals batch in Sunday's `insight-generator` Pass 10 weekly review. 3-cap, fingerprint cooldown identical to Pass 9.

This gives the user fast feedback when something obvious emerges (a new client lands hard) AND quiet weekly review for ambiguous patterns.

### Pass 11 — Person/role-change proposals (folded into Don't Forget weekly synthesis, v2.10.2)

Already shipped in v2.10.2. Listed here for completeness — the people layer's proactive surfacing happens inside Don't Forget, not in insight-generator.

---

## Invariants

1. Every event has exactly one `primary_thread_id`.
2. Every thread has exactly one `affiliation_id` pointing to an org (or `personal`).
3. `parent_org_id` forms a DAG with no cycles.
4. `parent_thread_id` forms a DAG with no cycles.
5. `classification_confidence ∈ [0.0, 1.0]`.
6. Reclassification produces a `reclassification` event; it never mutates a prior event's fields silently.
7. *(v2.10.3)* Every active org has exactly one `tier`; tier transitions write a `tier_change` event for audit.
8. *(v2.10.3)* Project status changes from auto-transition (active→dormant, dormant→archived) write a `status_change` event with `triggered_by: auto` and the inactivity duration.

cleanup validates all eight.
