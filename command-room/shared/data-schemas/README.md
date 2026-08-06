# Data Schemas — v2.2

This folder holds the JSON schemas that define every structured context file in a v2.2 workspace.

## Files

- `entities.schema.json` — canonical registry of people, projects (threads), orgs — with nested org support (parent_org_id, scope, relationship_type, is_primary_focus). Also defines the optional, dormant-by-default `brand` object (`$defs/brand`) — an OUT1 deliverable theme that may live on `workspace` (all docs) or an `org` (that org's docs); absent = premium defaults from `shared/scripts/brand.py`. Set by hand during a paid customization engagement; never touched by onboarding.
- `events.schema.json` — append-only event log (meetings, decisions, commitments, reclassifications, classification_reviews, etc.) with multi-thread tagging and classifier confidence
- `aliases.schema.json` — raw-form to canonical-id registry

## The Org Tree Model (v2.2)

Workspaces have a **graph of orgs**, not a flat home/side split. Each org carries three independent fields that together describe the CEO's relationship to the entity:

- **scope** (`holding` | `operating` | `division` | `brand` | `fund` | `other`) — what the org *is* in the tree
- **relationship_type** (`operating` | `partner` | `board` | `advisory` | `investment` | `client` | `portfolio_company` | `beneficiary` | `other`) — what the CEO *does* vis-à-vis the org
- **is_primary_focus** (boolean) — whether this org bubbles to the top of briefings

The CEO can mark **any number** of orgs as `is_primary_focus: true`. That covers portfolio / holding-co operators (e.g., a [Holding Co] CEO with three operating brands), as well as solo operators (exactly one primary focus).

Nested hierarchies use `parent_org_id`. Example:

```
org_acme_co         (scope: holding, relationship_type: operating, is_primary_focus: true)
  ├── org_acme_restaurant  (scope: operating, parent_org_id: org_acme_co)
  ├── org_acme_food_truck  (scope: operating, parent_org_id: org_acme_co)
  ├── org_acme_bakery      (scope: operating, parent_org_id: org_acme_co)
  └── org_acme_catering    (scope: operating, parent_org_id: org_acme_co)
```

This hierarchy drives affiliation-aware behavior across the product:
- Name resolution prefers the affiliation matching current context
- Briefings render one top section per `is_primary_focus` org; holdings expand to show operating children nested beneath; remaining orgs roll up into an OTHER ORGS section grouped by `relationship_type`
- "new thread" infers the most specific org from context (attendees, domains, explicit hints) — operating-level first, not holding

### Reserved ids

No reserved ids in v2.2. Slugs are connector-inferred during onboarding (email domains, Slack workspace names, Drive folder roots) and confirmed with the CEO in Phase 2. The legacy `org_home` id is accepted by readers for back-compat but is no longer minted.

### Thread (project) affiliation

Every thread MUST link to its org via `org_id` — the canonical thread→org field (ENTITY1); `affiliation_id` is the legacy alias, still read as a fallback on records that carry it but never newly written (the writer normalises it to `org_id`). The link should resolve to the **most specific** level (operating > holding). See `references/ORG_AND_THREAD_MODEL.md` for the full spec including threads that span multiple orgs (use `related_thread_ids[]` at the event level, not a forced duplicate thread).

### Person.primary_org_id + affiliation_ids[]

Every person has a `primary_org_id` (single org) and optional `affiliation_ids[]` (multiple). People often belong to more than one org — co-founder of A and board member of B — and the schema models this directly.

## Thread Kinds (v2.1)

The `project` entity in the schema is a **generalized tracked unit of attention** — not only a traditional project. The `kind` field carries the semantic load. Every CEO's workspace is a mix of these:

| kind | meaning | typical affiliation |
|---|---|---|
| `initiative` | internal work / program inside a primary-focus org | primary-focus |
| `deal` | specific transaction or opportunity (customer, acquisition, partnership) | any operating org |
| `advisory` | external advisory / consulting engagement | any org with relationship_type=advisory |
| `investment` | passive investment, LP stake | any org with relationship_type=investment |
| `board` | board seat | any org with relationship_type=board |
| `relationship` | an important person tracked as a standing thread, independent of any single deal (key customer, key partner, investor relationship) | any |
| `theme` | a recurring topic that spans multiple threads ('pricing', 'hiring', 'fundraise', 'platform reliability') | primary-focus |
| `concern` | a problem being actively monitored, not yet an initiative | any |
| `ritual` | recurring cadence (weekly 1:1 with X, monthly board prep, quarterly review) | any |
| `personal` | family, health, personal finance, hobbies | personal |
| `other` | fallback | any |

**Kinds without a filesystem folder.** `relationship`, `theme`, `concern`, and `ritual` do not require a matching folder under workspace root. Their `folder_name` is a logical identifier; the writer helper will not require a matching folder for these kinds.

**Stage and status still apply across kinds.** Every thread carries a `status` (active | paused | blocked | archived) and optional `stage`. A `theme` can be "paused" when the CEO consciously parks it. A `ritual` is typically "active" with a cadence reflected in its events.

### Why this matters for the product

- "What's going on" briefings section by kind: initiatives first, then deals, then relationships to nurture, then themes that are drifting, then rituals coming up.
- Insight-generator detects cross-kind patterns ("three deals and two relationships raised the same concern this week").
- Dormant-scan flags threads of any kind that haven't been touched — a relationship gone cold matters differently from a stalled deal, but both get surfaced.
- Commitment-aging applies across kinds — a commitment inside a ritual is still a commitment.

## Onboarding Seed Pack (optional pre-install input)

`onboarding_seed.schema.json` defines `ONBOARDING_SEED.json` — an optional file the operator drops at the client workspace root **before** install day, distilled from a recorded pre-onboarding interview with the client. It is input TO onboarding, not a workspace file: if present, Phase 1a ingests declared orgs/projects/people/aliases/priorities as **anchor truth** (same authority as the primary-affiliation gate — the connector scan enriches and adds, never overrides), pre-answers Phase 0 setup questions it covers (timezone, brain name), pre-loads aliases.json, feeds voice notes into BRAND_VOICE.md, then moves the file to `_hq/data/onboarding-seed.json` and appends an `onboarding_seed_ingested` event. Absent file = zero behavior change.

Entities in the pack are referenced by **name**, not id — ids are minted during onboarding, and every name + alias becomes an aliases.json mapping.

## Seed files

`seed/` contains empty starter versions of each file. Onboarding copies these to `_hq/data/` to initialize a new workspace:

```
_hq/data/entities.json   ← seed/entities.json
_hq/data/events.jsonl    ← seed/events.jsonl
_hq/data/aliases.json    ← seed/aliases.json
```

After seeding, onboarding adds the user's identity (first person record) and initial projects.

## Validation

`weekly-audit` validates all three files against these schemas on every run. Violations append to `_hq/CONFLICTS.md` with type `schema-violation`.

For a deeper description of each field, payload, and event type — see `references/DATA_CONTRACT.md`.
