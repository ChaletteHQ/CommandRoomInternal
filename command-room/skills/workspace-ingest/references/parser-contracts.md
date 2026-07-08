# Parser contracts — in-memory collection field examples

All parsers (A/B/C/D/E/F) emit the same in-memory collections with the same field
contracts: **orgs[]**, **people[]**, **threads[]**, **events[]**, **aliases[]**. These flow
into Phase 4 writes. The SKILL.md "Parser contracts" section keeps the prose contract
(enums, event carry-fields, confidence bands, the `project_` prefix note); the full JSON
field examples live here.

## orgs[]

```json
{
  "org_id": "org_holding_co",
  "canonical_name": "[Holding Co]",
  "scope": "operating",
  "relationship_type": "operating",
  "is_primary_focus": true,
  "parent_org_id": null,
  "domains": [],
  "slack_workspace_ids": [],
  "inferred_from": ["people-md-section", "connector-gmail-domain"],
  "first_seen": "2025-01-15",
  "last_interaction": "2026-04-20",
  "notes": ""
}
```

`scope` ∈ {holding, operating, division, brand, fund, vendor, other}. `relationship_type` ∈ {operating, partner, board, advisory, investment, client, portfolio_company, beneficiary, vendor, referral, other}. `is_primary_focus` confirmed in onboarding Phase 2c, not at parse time.

## people[]

```json
{
  "person_id": "person_001",
  "canonical_name": "[CEO]",
  "aliases": ["M", "Mira"],
  "role": "Founder",
  "email": "ceo@example.com",
  "phone": null,
  "primary_org_id": "org_holding_co",
  "affiliation_ids": ["org_holding_co"],
  "status": "active",
  "communication_style": "",
  "first_contact": null,
  "last_interaction": "2026-04-21",
  "last_interaction_channel": null,
  "notes": "",
  "project_ids": []
}
```

## threads[]

```json
{
  "thread_id": "project_001",
  "display_name": "NorthStar margin analysis",
  "folder_name": "NorthStar",
  "kind": "advisory",
  "stage": "active",
  "status": "active",
  "affiliation_id": "org_northstar",
  "owner_person_id": "person_001",
  "stakeholder_person_ids": [],
  "last_activity": "2026-04-15",
  "first_seen": "2026-01-10",
  "next_step": "",
  "notes": "",
  "parent_thread_id": null,
  "spawned_from_thread_id": null,
  "cross_refs": []
}
```

The `project_` prefix on `thread_id` is retained for schema stability per the plugin-level `references/ORG_AND_THREAD_MODEL.md`; user-facing vocabulary is "project" (not "thread").

## aliases[]

```json
{
  "raw": "M",
  "canonical_id": "person_001",
  "confidence": 1.0,
  "added_ts": "2026-04-21T12:00:00Z",
  "added_by": "workspace-ingest"
}
```

Confidence bands:
- **1.0** — explicit alias (parenthetical in name header, explicit "aka" / "also known as" phrase)
- **0.9** — high-signal inference (email alias + name match, folder name vs display name)
- **0.7** — weak inference (glossary mention, contextual usage)
