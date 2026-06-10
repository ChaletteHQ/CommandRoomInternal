# Workspace Schema — v1.4

Canonical contract between Command Room skills and user workspace structure. All skills reference this schema to ensure consistent file/folder creation, naming, and compatibility.

## Root Structure

```
[WORKSPACE_ROOT]/
├── CLAUDE.md                      # Working memory hot cache (auto-loaded by Cowork each session)
├── _hq/                           # Workspace control center
├── _people/                       # Team intelligence (leadership team, key people)
├── _exploring/                    # Active exploration items
├── _archive/                      # Completed/archived projects
└── [Project Name]/                # Project folders (match MASTER_TRACKER.md names exactly)
```

## CLAUDE.md (Working Memory — Auto-Generated)

Hot cache file that Cowork loads automatically every session. Contains user identity, active projects, top 15-20 contacts with nicknames, decoded shorthand/terms, quick command reference, and session rules. ~80 lines max.

**File creation rules:**
- Onboarding Phase 4 generates it from onboarding data
- update-check auto-generates it for v1.3.x→v1.4.0 upgrades (reads existing _hq/ files)
- "end session" does surgical updates: new people, project status changes, new terms
- Promotion/demotion keeps file under 80 lines

**Template:** `references/claude-md-template.md`

## _hq/ (Headquarters — Mandatory)

Core workspace memory and coordination files. Created by onboarding; maintained by all skills.

```
_hq/
├── MASTER_TRACKER.md              # Source of truth: all active projects, status, dates
├── BUSINESS_CONTEXT.md            # Company/situation context, goals, constraints
├── DECISION_LOG.md                # Decisions made + rationale + dates
├── PEOPLE.md                      # Key contacts, roles, relationships
├── BRAND_VOICE.md                 # [Optional] Brand voice guidelines (created if user has brand material)
├── COMMUNICATION_PROFILE.md       # [Optional] CEO-facing communication style doc (created during onboarding)
├── briefings/                     # Auto-created by workspace-manager skill
│   └── [briefing-name].md         # On-demand briefings
├── summaries/                     # Auto-created by executive-summary skill
│   └── [summary-name].md          # Weekly/periodic summaries
├── audit-reports/                 # Auto-created by audit-command skill
│   └── [audit-date].md            # Health check reports
└── intel/                         # Knowledge base and research index
    ├── INDEX.md                   # Navigation + curated links to all intel
    └── KNOWLEDGE_BASE.md          # Searchable reference material
```

**File creation rules:**
- Onboarding creates: MASTER_TRACKER.md, BUSINESS_CONTEXT.md, DECISION_LOG.md, PEOPLE.md, COMMUNICATION_PROFILE.md (if email scan available)
- workspace-manager creates briefings/ on first use
- executive-summary creates summaries/ on first use
- audit-command creates audit-reports/ on first use
- intel/ created by onboarding; INDEX.md + KNOWLEDGE_BASE.md may be populated by intel-intake

## _people/ (Team Intelligence — Optional)

CEO's private profiles on their leadership team and key people. Created during onboarding Phase 3 (Team Discovery) or on first "add [name] to my team."

```
_people/
├── _team-config.md                # Roster, prep format, staleness rules
├── [name].md                      # One PERSON.md per tracked team member
├── [name].md                      # Filename: lowercase, hyphenated (e.g., philippe.md, dave-coo.md)
└── prep/                          # Auto-created by team-intelligence skill
    └── Prep_[name]_[DATE].md      # 1:1 prep briefs
```

**File creation rules:**
- Onboarding Phase 3 creates: `_people/`, `_team-config.md`, initial PERSON.md files
- team-intelligence creates: new PERSON.md files on "add to team", `prep/` folder on first 1:1 prep
- workspace-manager updates: PERSON.md Interaction Log + Commitments during "what's going on" and "end session"
- meeting-notes updates: PERSON.md Interaction Log + Commitments after processing transcripts
- team-intelligence reads: all files in `_people/` + all PROJECT_BRAIN.md files for cross-project views

**Graceful degradation:**
- If `_people/` doesn't exist, all skills skip team-related steps silently
- If `_team-config.md` is missing, use defaults (14-day staleness, 3-day overdue, standard prep)
- If a PERSON.md is referenced but missing, offer to create it

**File size targets:**
| File | Target | Notes |
|------|--------|-------|
| `_team-config.md` | <1 KB | Roster + settings only |
| `[name].md` (PERSON) | <3 KB | Interaction log auto-archives at 10 entries |
| `Prep_[name]_[DATE].md` | <2 KB | Point-in-time brief |

---

## _exploring/ (Active Exploration)

Temporary workspace for items being explored but not yet in active projects.

```
_exploring/
└── [item-name]/
    └── notes.md                   # Exploration notes, links, decisions
```

**File creation rules:**
- Created on-demand by skills (call-prep, intel-intake, etc.)
- User can move items to projects or archive manually
- Auto-cleanup not enforced; user controls lifecycle

## _archive/ (Completed Projects)

Container for completed or inactive projects. Structure mirrors active project folders.

```
_archive/
└── [Project Name]/                # Same structure as active projects
    ├── PROJECT_CONTEXT.md
    ├── SESSION_NOTES_[NAME].md
    └── ref/, meetings/ (as applicable)
```

## Project Folders (Active — per [Project Name])

One folder per active project listed in MASTER_TRACKER.md. Folder name must match exactly.

```
[Project Name]/
├── PROJECT_CONTEXT.md             # Project brief: background, scope, success criteria, stakeholders
├── PROJECT_BRAIN.md               # Institutional memory: people, gotchas, threads, workflows, aliases
├── SESSION_NOTES_[NAME].md        # One per project, always at project root. [NAME] = user's first name
├── ref/                           # Created as needed
│   ├── contacts.md                # Project-specific contacts
│   ├── scope.md                   # Detailed scope, constraints, exclusions
│   ├── financials.md              # Budget, pricing, costs
│   ├── risks.md                   # Known risks + mitigation
│   └── timeline.md                # Milestones, deadlines, schedule
└── meetings/                      # Created as needed
    └── Call_Prep_[DATE].md        # One per meeting; [DATE] = YYYY-MM-DD
```

**File creation rules:**
- Onboarding creates: PROJECT_CONTEXT.md, PROJECT_BRAIN.md (starter), SESSION_NOTES_[NAME].md
- workspace-manager creates PROJECT_BRAIN.md on "new project" (seeded from scans) and auto-updates it on "end session"
- If PROJECT_BRAIN.md is missing on "go [project]", proceed without it — it gets created on the next "end session"
- Skills create ref/ files on-demand when needed (graceful degradation if missing)
- call-prep creates meetings/ and Call_Prep_*.md files
- workspace-manager may update PROJECT_CONTEXT.md periodically

## Naming Conventions

| Element | Format | Example | Notes |
|---------|--------|---------|-------|
| Workspace root | `[WORKSPACE_ROOT]` | Resolved at skill runtime | User's mounted folder |
| User first name | `[NAME]` | `Alex` | Set during onboarding; used identically everywhere |
| Session notes file | `SESSION_NOTES_[NAME].md` | `SESSION_NOTES_Alex.md` | Exactly one per project |
| Date format | `YYYY-MM-DD` | `2026-04-09` | ISO 8601; used in filenames + content |
| Call prep file | `Call_Prep_[DATE].md` | `Call_Prep_2026-04-09.md` | Title case "Call_Prep" |
| Project folders | Exact match to MASTER_TRACKER.md | If tracker says "Acme Corp", folder = `Acme Corp/` | Case-sensitive |
| Person profile files | Lowercase, hyphenated, disambiguated | `philippe.md`, `dave-coo.md` | Match _team-config.md roster |
| 1:1 prep files | `Prep_[name]_[DATE].md` | `Prep_philippe_2026-04-09.md` | Title case "Prep" |
| Briefing/summary/audit files | Descriptive, lowercase with hyphens | `weekly-summary.md`, `q2-financial-audit.md` | Keep under 40 chars |

## File Creation Rules

### Created by Onboarding (Mandatory)

- `_hq/MASTER_TRACKER.md`
- `_hq/BUSINESS_CONTEXT.md`
- `_hq/DECISION_LOG.md`
- `_hq/PEOPLE.md`
- `_hq/intel/` (folder)
- `_hq/intel/INDEX.md`
- `_hq/intel/KNOWLEDGE_BASE.md`
- `[Project Name]/PROJECT_CONTEXT.md` (for each initial project)
- `[Project Name]/PROJECT_BRAIN.md` (for each initial project — seeded from scans/answers)
- `[Project Name]/SESSION_NOTES_[NAME].md` (for each initial project)

### Created On-Demand by Skills (Optional)

| Skill | Files Created | Condition |
|-------|---------------|-----------|
| workspace-manager | `[Project]/PROJECT_BRAIN.md` | Created on "new project"; auto-updated on "end session" |
| workspace-manager | `_hq/briefings/` + briefing files | On demand when user requests briefing |
| executive-summary | `_hq/summaries/` + summary files | On demand when user requests summary |
| audit-command | `_hq/audit-reports/` + audit files | On demand when user runs audit |
| call-prep | `[Project]/meetings/Call_Prep_[DATE].md` | When preparing for a meeting |
| intel-intake | Populates `_hq/intel/KNOWLEDGE_BASE.md` | When processing new intel/research |
| meeting-notes | Updates `[Project]/SESSION_NOTES_[NAME].md` | When processing meeting transcript |
| team-intelligence | `_people/[name].md`, `_people/prep/Prep_*.md` | On team queries, 1:1 prep |

### Auto-Created Folders

These folders are created automatically when a skill needs them (no user action required):

- `_hq/briefings/`
- `_hq/summaries/`
- `_hq/audit-reports/`
- `_people/` (on first team member add or during onboarding Phase 3)
- `_people/prep/` (on first 1:1 prep)
- `[Project]/ref/`
- `[Project]/meetings/`

## Graceful Degradation & Compatibility

### If Expected File Missing

| File | Skill | Behavior |
|------|-------|----------|
| `SESSION_NOTES_[NAME].md` | meeting-notes | Create it at project root with standard header |
| `PROJECT_BRAIN.md` | workspace-manager | Graceful skip on "go [project]"; auto-created on next "end session" |
| `PROJECT_CONTEXT.md` | Any skill | Use fallback context from MASTER_TRACKER.md |
| `MASTER_TRACKER.md` | Any skill | Warn user; halt non-critical operations |
| `ref/contacts.md` | Any skill | Graceful skip; don't fail |
| `Call_Prep_[DATE].md` | meeting-notes | Create it if missing; append to file if exists |
| `_people/` folder | workspace-manager, meeting-notes | Skip team-related steps silently |
| `_team-config.md` | team-intelligence | Use defaults (14-day staleness, 3-day overdue) |
| `_people/[name].md` | team-intelligence | Offer to create: "Want me to add them?" |

### If Unexpected File/Folder Found

All skills follow this rule:
- **Ignore unexpected files/folders completely**
- Do not error or warn
- Do not delete, modify, or reorganize user content
- Proceed normally with schema-compliant operations

Example: If user has `[Project]/notes.md` (non-standard), skills ignore it and continue working with standard files.

## Version & Compatibility

| Schema Version | Plugin Version | Release Date |
|---|---|---|
| 1.0 | v1.1.0+ | 2026-04-09 |
| 1.1 | v1.2.0+ | 2026-04-09 | Added PROJECT_BRAIN.md |
| 1.2 | v1.3.0+ | 2026-04-09 | Added _people/ (team intelligence) |
| 1.3 | v1.4.0+ | 2026-04-13 | Added CLAUDE.md working memory to root |
| 1.4 | v1.6.0+ | 2026-04-14 | Added COMMUNICATION_PROFILE.md to _hq/ |

Breaking changes to schema trigger version bump. Skills must declare minimum schema version in SKILL.md.

Example:
```yaml
# In skill SKILL.md
required_schema_version: "1.0"
```

## File Size Targets

Recommended maximum file sizes (for usability):

| File | Target | Notes |
|------|--------|-------|
| `MASTER_TRACKER.md` | <2 KB | Keep concise; link to PROJECT_CONTEXT for details |
| `SESSION_NOTES_[NAME].md` | <10 KB/year | Annual rollover recommended |
| `PROJECT_CONTEXT.md` | <3 KB | Executive summary; detailed plans in ref/ |
| `PROJECT_BRAIN.md` | <4 KB | Grows over time; prune resolved threads periodically |
| `DECISION_LOG.md` | <5 KB | Keep recent entries; archive old ones annually |
| `Call_Prep_[DATE].md` | <2 KB | Focus on prep points; outcome recorded in SESSION_NOTES |

---

**Last updated:** 2026-04-14  
**By:** Command Room v1.6.1  
**Schema version:** 1.4
