---
name: list-active
surfaces: both
description: "Zero-interaction list of all projects in the workspace — the org tree with canonical names, aliases, and last-activity dates. Triggers: 'list projects', 'list active projects', 'list all projects', 'active projects', 'project list', 'review my projects', 'show projects', 'show me projects', 'what projects', 'what projects do I have', 'show archived', 'roster', 'project roster'. DOES NOT fire on 'what's going on' or 'workspace status' (full daily briefing — workspace-manager) or 'lets work' (silent load — workspace-manager) or 'new project' (project creation — workspace-manager)."
---

## Skill Boundary (v2.1)

- **Use list-active for:** instant recall of what projects exist when you've forgotten a name. Renders the full org tree inline in chat. No classification, no briefing, no scan — just the list.
- **Use `workspace-manager` for:** engaging a specific project ("go [project]"), starting/ending sessions, or the full "what's going on" daily briefing.
- **Use `morning-briefing` for:** the daily digest with calendar + email + urgency flags.

list-active is a 5-second discovery tool. It is NOT a briefing and NOT a status scan.

## Personification Contract (v3.13.8.4+)

Before rendering the tree, read the canonical voice spec at `shared/PERSONIFICATION.md` and call `get_brain_name(workspace_root)` from `shared/scripts/personification.py`. The tree footer (last line, after the render) is a single line: `"— {brain_name}"`. Default `{brain_name}` is `"Penelope"`. No intro line — list-active is a zero-interaction tool, the footer is the only personification surface.

## Writer Contract

Read-only. No events written. No entities mutated. No connector calls. Pure render over existing `entities.json` + `events.jsonl`.

---

# List Active — Project Roster

Render the full org tree with every project (active, dormant, archived optionally filtered) so the CEO can see canonical names and aliases at a glance. Designed for the "wait, what did I call that?" moment mid-session.

## Triggers

| Phrase | Mode |
|--------|------|
| `list projects` / `list active projects` / `active projects` | default (active only) |
| `project list` / `show projects` / `show me projects` | default |
| `what projects` / `what projects do I have` | default |
| `roster` / `project roster` | default |
| `list all projects` / `show archived` | include archived |

Default mode filters out `status: archived` projects. Archived view includes everything.

## How It Works

### Step 1: Read the data layer

1. `[WORKSPACE_ROOT]/_hq/data/entities.json` — orgs + projects + people
2. `[WORKSPACE_ROOT]/_hq/data/events.jsonl` — compute `last_activity` per project by scanning most recent event per `primary_thread_id`
3. `[WORKSPACE_ROOT]/_hq/data/aliases.json` — for each project, collect any aliases that point to it

If `entities.json` doesn't exist (pre-v2 workspace), fall through to **folder fallback** (Step 4).

### Step 2: Build the tree

Group projects under their orgs. Order:

1. **Primary focus orgs** (where `is_primary_focus: true`), grouped with their children:
   - Holdings first (those with `scope: holding`), their operating children nested underneath
   - Standalone operating orgs next (no parent)
2. **Advisory** orgs (`scope: advisory` or `relationship_type: advisor`)
3. **Other** orgs (`scope: beneficiary` / `other` / `personal`)
4. **Workspace-level projects** (projects with `affiliation_id` = workspace, no org parent) at the end

Within each org, sort projects by `last_activity` descending (freshest first).

### Step 3: Render

Compact, scannable. One project per line. Format:

```
PRIMARY FOCUS

[Holding Co] (holding)
  ├─ Command Room (product) · last Apr 21
  └─ _[Company] HQ_ (operating) · last Apr 21

NorthStar (operating · client)
  ├─ Margin analysis · last Apr 19
  └─ Sales materials · last Apr 12

Acme Property (operating · aka [Operating Co], Property Alpha) · last Apr 18
  ├─ Property Alpha remediation · last Apr 18
  │   └─ Vendor coordination · last Apr 17
  └─ GC entity formation · last Apr 10

Acme Co (operating · aka acme, the sourcing co)
  └─ Sourcing bot rollout · last Apr 15
      ├─ Vendor onboarding · last Apr 15
      └─ Bid leveling · last Apr 14

TalentCorp (operating) · last Apr 12

ADVISORY

Traders Inc (advisory · aka the trading group, quant desk) · last Apr 19

OTHER

Personal · last Apr 5

NOT TIED TO A COMPANY

(none)
```

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "WORKSPACE-LEVEL" / "Total: 12 projects across 4 orgs."
- GOOD: "NOT TIED TO A COMPANY" / "Total: 12 projects across 4 companies."

Rules:
- Canonical org/project name first.
- Aliases in parens after the name when aliases.json has entries.
- `scope` and/or `relationship_type` in parens when useful (e.g., "operating · client", "holding", "advisory").
- Sub-projects (those with `parent_thread_id` set) nest under their parent using `├─`/`└─`.
- `last [date]` on every line. When no events exist for that project, render `last —` (see Edge Case B) — never omit the line.
- Empty sections render as `(none)`. Do not omit the section header.

### Step 4: Folder fallback (no entities.json)

If `_hq/data/entities.json` doesn't exist:

1. Scan `[WORKSPACE_ROOT]/` for top-level folders.
2. Skip: `_hq`, `_archive`, `_people`, anything starting with `.` or `_`.
3. For each folder, look for `SESSION_NOTES_*.md`. Use its mod time as the activity date.
4. Render flat list (no org hierarchy available):

```
PROJECTS (showing folders only — workspace not fully set up yet)

NorthStar · last Apr 19
Acme Property · last Apr 18
Acme Co · last Apr 15
TalentCorp · last Apr 12
Skyler · last Apr 19

Say "onboard me" to set up the full project view.
```

Fallback output always includes the onboarding hint at the bottom.

## Edge Cases

**A. Empty workspace (no projects at all):**
Render: `No projects found. Say "onboard me" to set up your workspace, or "new project [name]" to create one.`

**B. Project exists in entities.json but has zero events:**
Render the line with `last —` instead of a date. Do not omit.

**C. Orphan project (project with `affiliation_id` pointing to a missing org):**
Render under a `NEEDS A HOME` section at the bottom with a one-line note: `This project isn't linked to a company yet. Say "cleanup" and I'll sort it.`

**D. Aliases list too long:**
If a project has more than 3 aliases, render the first 3 followed by `+N more` — full list available via `show aliases for [project]` (handled by workspace-manager).

**E. Sub-project deeper than 2 levels:**
Support arbitrary depth in the tree with indented `├─`/`└─`, but cap visible depth at 3. If deeper nesting exists, render `+N deeper — say 'expand [project]' to see` (workspace-manager handles the expand).

**F. Very large workspace (>50 projects):**
Render the full tree anyway — this is discovery, not briefing. Long output is the right answer. Add a footer: `Total: [N] projects across [M] companies.`

**G. Archived vs active filter:**
Default mode excludes projects with `status: archived`. When the user triggers with `list all projects` or `show archived`, include those with an `[archived]` suffix on the line.

## Implementation Notes

- The reference script `render_tree.py` does the org-tree traversal + last-activity computation. It's a pure read over entities.json + events.jsonl and can be invoked directly or embedded as the render logic inside the skill response.
- Last-activity date is computed from the most recent event where `primary_thread_id` equals the project's id. Events where the project appears only in `related_thread_ids[]` do NOT count toward last-activity (cross-refs don't promote the thread's primary activity date).
- This skill does not mutate state and does not interrupt the CEO with follow-up prompts — it renders and returns.

## Cross-skill handoff

- **workspace-manager** — after the list is rendered, the CEO typically says "go [project name]" which hands off to workspace-manager for the actual engagement.
- **morning-briefing** — can reference list-active at the top: "You have [N] active projects." (soft link; morning-briefing remains its own skill)
- **cleanup** — uses the same org-tree rendering logic for its audit report; they can share the `render_tree.py` helper.

## What It Doesn't Do

- Does not scan connectors (no Gmail/Calendar/Slack reads). Pure data-layer render.
- Does not interpret activity (no "this project is stuck" commentary — that's insight-generator).
- Does not archive, create, or modify projects (workspace-manager owns lifecycle).
- Does not produce a report file — the output is chat-only.
- Does not surface people or relationships — that's people-crm.
- Does not render briefing content (calendar, email, Slack) — that's morning-briefing.
