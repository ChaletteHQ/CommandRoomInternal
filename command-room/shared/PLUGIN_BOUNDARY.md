# Plugin Boundary — Data Safety Manifest (v2.1)

**Purpose:** Every CEO's workspace contains their most sensitive business information. This contract defines the hard boundary between **plugin code** (shared across customers, shipped via the marketplace) and **customer data** (lives only in the customer's workspace, never crosses the boundary).

**Rule of thumb:** *Skills are code. Data is data. Never mix.*

If this contract is violated in any way, the affected customer's trust is lost. This is the single highest-priority invariant in the plugin.

---

## The Boundary

```
┌─────────────────────────────────────────────┐
│ PLUGIN (shared, shipped, updated)           │
│                                             │
│  skills/*/SKILL.md     (instructions)       │
│  shared/*.md           (contracts)          │
│  references/*.md       (templates, specs)   │
│  plugin.json           (manifest)           │
│                                             │
│  ↑ READ by Claude at runtime                │
│  ↑ VERSIONED in marketplace                 │
│  ↑ SAME for every customer                  │
│                                             │
└────────────────────┬────────────────────────┘
                     │  one-way
                     │  (plugin is read-only
                     │   at customer runtime)
                     ▼
┌─────────────────────────────────────────────┐
│ CUSTOMER WORKSPACE (per-customer, private)  │
│                                             │
│  _hq/data/entities.json                     │
│  _hq/data/events.jsonl                      │
│  _hq/data/aliases.json                      │
│  _hq/VOICE_SAMPLES.md                       │
│  _hq/BUSINESS_CONTEXT.md                    │
│  _hq/briefings/*                            │
│  _hq/insights/*                             │
│  _hq/intel/*                                │
│  _hq/views/*                                │
│  _people/*                                  │
│  [Project]/*                                │
│                                             │
│  ↑ WRITTEN by skills                        │
│  ↑ NEVER leaves this workspace              │
│  ↑ DIFFERENT for every customer             │
│                                             │
└─────────────────────────────────────────────┘
```

---

## Invariants

### 1. Plugin files contain NO customer data

Every file under `plugin-source-v2/` (skills, shared, references, seed files) is identical across every customer install. It contains:
- Instructions
- Schemas
- Templates
- Contracts
- Empty seed files (with no records)

It does NOT contain:
- Customer names, projects, people, emails, org names
- Example payloads that include real customer data
- Any string that identifies a real CEO or their business

Violation check: `grep` the plugin source for any known customer strings before every version bump. If found, redact.

### 2. Customer data writes stay in the customer workspace

Every write by every skill lands under the customer's workspace root (resolved at runtime as `[WORKSPACE_ROOT]`). No skill writes to:
- The plugin's own directory (`plugin-source-v2/...`)
- Any path outside `[WORKSPACE_ROOT]`
- Any shared location (cloud bucket, analytics pipeline, telemetry endpoint)

The only exceptions are connectors (Gmail, Slack, Calendar) where the write is to the customer's own account via their OAuth — never to a shared account.

### 3. Telemetry — customer-facing skill retired; internal pack-run metrics still active

**Customer-facing telemetry skill: retired v3.9.0.** The `beta-telemetry` user-facing skill (which once asked customers to opt in to plugin-developer telemetry) moved to the chalette internal plugin (`chaletteholdings/chalette` v0.5.0+) and is Matthew-only, never customer-facing in Command Room.

**Internal pack-run telemetry: still active and customer-local.** Every scheduled-task fire (`cr-morning-brief`, `cr-inbox`, `cr-commitments`, etc.) emits a `pack_run` event to the customer's own `_hq/data/events.jsonl` via `shared/scripts/telemetry.py::build_pack_run_telemetry()`. This is per-workspace metrics (token usage, connector call counts, duration) used by the `usage-report` skill to answer "where is my Command Room spend going?" — the data never leaves the customer's workspace and is not aggregated anywhere. This is workspace-local diagnostics, not telemetry-the-developer-feedback-loop.

Any future telemetry layer that EXFILTRATES data from the customer workspace MUST:
- Be off by default.
- Be clearly disclosed to the customer during onboarding with explicit opt-in.
- Emit ONLY: skill name, invocation count, error code, workspace id hash (salted, non-reversible).
- NEVER emit: entity names, event contents, email subjects, document titles, or any string derived from customer files.

Any exfiltrating telemetry payload that could include customer data is a bug.

### 4. Plugin updates do not read customer data

When a new plugin version is installed, the installer:
- Replaces `plugin-source-v2/` with the new version.
- Does NOT read or transmit anything under `[WORKSPACE_ROOT]`.
- Does NOT modify anything under `[WORKSPACE_ROOT]` (migrations, if any, run via the `migration-v2` skill on the customer's next session, logged locally).

### 5. Cross-customer data never exists

There is no shared data layer. There is no "aggregate insights across customers." There is no "benchmark your metrics against other customers." Every install is an island. Feature proposals that require cross-customer aggregation are out of scope for this plugin.

### 6. Connector data respects the boundary

When skills read from Gmail / Calendar / Slack / Granola / Drive:
- Reads happen via the customer's own OAuth token.
- Results are stored in the customer's workspace (events.jsonl, briefings).
- Results are NOT stored in plugin files.
- Results are NOT transmitted to any non-customer endpoint.

When skills summarize connector content for the CEO, the summary lives in the customer's workspace. The raw content does not persist in the plugin anywhere.

### 7. Skills never hard-code customer-specific content

No SKILL.md contains literal text like "When working with Acme Corp..." or "The operator prefers..." If a skill needs customer-specific behavior, it reads `BUSINESS_CONTEXT.md` or a specific customer-controlled config file. The skill code itself is generic.

---

## Customer Workspace = Private Forever

The customer workspace is the customer's private file system. Anthropic / the plugin author / Claude Code / anyone else does NOT have access to files under `[WORKSPACE_ROOT]` unless the customer explicitly shares them.

What the customer can rely on:
- Their `entities.json`, `events.jsonl`, `aliases.json` are theirs alone.
- Their voice samples and communications are theirs alone.
- Their business context, decisions, commitments, relationships are theirs alone.
- Uninstalling the plugin does not delete their workspace — the files remain; only the skill instructions disappear.

---

## What Skills CAN Do Across the Boundary

The boundary permits these cross-direction operations:

- **Plugin → Customer:** skills read instructions from plugin files and apply them to customer workspace files. Templates from `references/` are rendered with customer data. This is fine — instructions crossing into private use.
- **Customer → Plugin:** NEVER. There is no write from customer to plugin, no exception.

The only runtime information about customer usage that crosses back out of the workspace is:
1. Opt-in telemetry (per rule #3).
2. Error reports if the customer manually submits one (explicit act).
3. Feature feedback via the CLAUDE.md feedback channel (explicit act).

---

## Validation

`cleanup` runs these checks every Sunday:

1. `grep` all files under `[WORKSPACE_ROOT]/.claude/plugins/.../plugin-source-v2/` for strings matching canonical person names from customer's entities.json. Any hit = violation, surface immediately.
2. Check that no skill writes outside `[WORKSPACE_ROOT]` (by scanning for hard-coded paths in SKILL.md files).
3. Check that no `telemetry_*` events are being EXFILTRATED by any source_skill in this plugin (the customer-facing `beta-telemetry` skill was retired v3.9.0; residual customer-facing telemetry writes indicate a stale install). Per-workspace `pack_run` telemetry events are workspace-local and expected.
4. Check that connector results are not cached anywhere under plugin directory.

Violations log to `_hq/CONFLICTS.md` with type `boundary-violation`. These are the highest-severity conflicts — they block plugin auto-updates until resolved.

---

## Adding New Skills

Every new skill MUST be reviewed against this contract before merge:

- [ ] No literal customer names, projects, or entity references anywhere in the skill file.
- [ ] All writes resolve under `[WORKSPACE_ROOT]`.
- [ ] All reads stay within `[WORKSPACE_ROOT]` + connector scope.
- [ ] No network calls outside connector endpoints.
- [ ] No customer-facing telemetry skills in this plugin (retired v3.9.0; the chalette internal plugin is the home for any plugin-developer telemetry). Per-workspace `pack_run` events written to the customer's own events.jsonl are not telemetry — they are workspace-local diagnostics consumed by `usage-report`.
- [ ] Any customer-specific behavior is driven by `BUSINESS_CONTEXT.md` or a user-controlled config file, not hard-coded.

Skills failing review are blocked from the marketplace.

---

## Why This Matters

Every customer who installs the plugin is trusting that:
1. Their business data stays on their machine.
2. Their voice, relationships, and decisions are not visible to anyone else.
3. Plugin updates cannot exfiltrate data.
4. Their data cannot accidentally appear in another customer's workspace.

Violations destroy trust immediately and permanently. One boundary violation is plugin-ending. This is why the rule is structural, not policy: there is no shared data layer to violate. The boundary is enforced by architecture, not by good intentions.

---

**End of plugin boundary contract.**
