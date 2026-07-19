# Command Room

A personal operating system that makes Claude your chief of staff — not just a chatbot you re-explain everything to every time.

## What Changes After Setup

**Claude knows you.** Your business, your projects, your people, your preferences. You never re-explain context again.

**Everything you're juggling — tracked.** Every project, deal, idea, and commitment in one living system. It updates itself as you work. Nothing disappears.

**Nothing falls through the cracks.** Weekly health checks, staleness alerts, commitment tracking, and proactive flagging. The system does the nagging so no human has to.

**It compounds.** Day one, Claude knows the basics. Month three, it's been in every meeting, remembers every decision, knows every open thread. The longer you use it, the sharper it gets — without you doing any extra work.

## Getting Started

Connect your Gmail and Calendar, then start a new conversation. The Command Room detects it's a fresh install and starts automatically — no trigger phrase needed.

Here's what happens in about 12 minutes:

1. **Claude scans your tools** — reads your emails, calendar, Slack, and files. Narrates what it finds.
2. **The surprise** — surfaces one thing you forgot about (a missed commitment, cold thread, or overdue follow-up). This is the hook.
3. **The reveal** — shows everything it learned: your projects, people, team, communication style. You correct what's wrong and fill in what's missing.
4. **The build** — you watch your workspace get constructed from real data. Not templates — your actual projects, contacts, and commitments.
5. **The live briefing** — runs "what's going on" against your real data. You see your entire business organized for the first time.
6. **Real work** — onboarding ends with an actual task: prepping a meeting, drafting emails, or following up on something overdue.
7. **Tomorrow's preview** — shows what your morning briefing will look like. Sets up daily delivery via Slack or email.

You also get a **Communication Profile** — a breakdown of how you write based on your real emails. It's saved as a document you can keep, share, or refine.

## The Core Commands

**"what's going on"** — Your daily briefing. Open Cowork, say this, and know where everything stands. Checks email, calendar, and Slack for updates since your last session.

**"go [project]"** — Jump into a specific project. Loads all context — session notes, project docs, tracker status — and checks connected sources for anything new related to that project.

**"end session"** — Say this when you're done working. Saves everything, updates the tracker, carries forward open items. This is the save button — nothing persists without it.

**"new project [name]"** — Create a new project. Asks discovery questions, creates the folder and context docs, adds it to the tracker. Also works with "new exploring [name]" for early-stage ideas.

**"meeting notes"** — Paste a transcript or let it pull from Granola. Decisions, action items, people, and commitments cascade into the right places automatically.

**"audit"** — Quick health check. Scores your workspace, flags what's slipping, suggests fixes. Under 60 seconds.

**"list projects"** — Full roster of what you're working on, with canonical names and aliases. The "wait, what did I call that?" lookup.

**"prep call with [person]"** — Generates a meeting brief pulling from calendar, email, Slack, past notes, and commitments. Walk into any meeting prepared.

## Skills

Grouped by role. Core skills are the daily drivers. Infrastructure skills run weekly or on demand. Output-layer skills produce polished deliverables on request. Writing skills draft short-form prose (email, Slack, memos) with Voice Calibration v3.0 baked in. The complete current roster lives under `skills/` — the tables below highlight the major skills by category.

### Core (daily drivers)

| Skill | What It Does | Key Triggers |
|-------|-------------|-------------|
| **Workspace Manager** | The brain. Briefings, project engagement, session saves, project lifecycle. | "what's going on", "lets work", "end session", "go [project]", "new project [name]" |
| **Command Room Onboarding** | Guided first-time setup. Scans tools, interviews you, builds the workspace. | "set up my command room" |
| **Morning Briefing** | Proactive daily digest. Calendar, emails, overdue items. | "morning briefing", "brief me" (or scheduled) |
| **Meeting Notes** | Transcript → decisions, action items, commitments. Cascades into the data layer. | "process meeting", "meeting notes" |
| **Call Prep** | Meeting briefs from every source in seconds. | "prep me for", "prep call with" |
| **List Active** | Zero-interaction roster — full org tree with projects, aliases, last activity. | "list projects", "show projects", "roster" |

### Infrastructure (weekly or on-demand)

| Skill | What It Does | Key Triggers |
|-------|-------------|-------------|
| **Insight Generator** | Weekly pattern pass. Low-confidence classifications, project proposals (Pass 9), staleness signals. | "run insights", "review project proposals", "weekly insights" |
| **Weekly Audit** | Full interactive health check. Scores workspace, surfaces fixes, generates dashboard. Subsumes quick-audit + executive summary. | "weekly audit", "audit", "quick audit", "weekly summary" |
| **Decision Log** | Records decisions with rationale and alternatives. Searchable. | "log decision", "what did we decide about" |
| **People CRM** | Relationship tracker with live enrichment. | "who is [person]", "who haven't I talked to" |
| **Team Intelligence** | Person-first queries for leadership team. 1:1 prep, commitment tracking. | "prep me for my 1:1 with [name]", "team status" |
| **Intel Intake** | Articles, videos, content → connected to your projects. | paste any URL, "intel review" |
| **Automation Scanner** | Finds automatable processes in your workspace with ROI estimates. | "what can be automated", "automation scan" |
| **Workspace Ingest** | Two-layer ingest (v2.14.20+): pulls context (people, projects, decisions, memories) into the data layer + copies actual deliverable documents (employee files, 1:1 notes, contracts, etc.) into project folders with preview-and-confirm. Folder-mode absorbed the legacy `context-ingestion` skill. Bootstrap-mode absorbs the legacy `migration-v2`. Sources: prior workspaces, ChatGPT exports, Downloads / Desktop folders, custom markdown notes. | "ingest folder [path]", "scan my desktop", "sort my downloads", "ingest my chatgpt export", "migrate from v1.x" |

### CEO Output Layer (polished deliverables)

| Skill | What It Does | Key Triggers |
|-------|-------------|-------------|
| **One-Pager Composer** | Any topic → polished 1-page .docx brief. | "one-pager on [topic]" |
| **Follow-Up Ritual** | Transcript → summary + action items + per-attendee email drafts in 60 seconds. | "follow-up ritual", "follow up on the call" |
| **Dormant Customer Scan** | Surfaces customers gone dark vs historical cadence. Ranked by revenue × dormancy. | "dormant customers", "who's gone quiet" |
| **Pipeline Tracker** | Deal tracking on the workspace substrate — stages, next steps, rot flags, won/lost with reasons. Ranked report, one-tap moves. | "show my pipeline", "new deal", "mark [deal] won" |
| **Inbox Triage** | Morning inbox pass. Five-bucket classification + top-5 surfacing + reply drafts. | "triage inbox", "inbox triage" |
| **Speech Prep** | Audience analysis + 3-point outline + hooks + Q&A prep. | "speech prep", "help me prepare for [audience]" |
| **Stress Test** | Munger-method inversion. Maps paths to failure, reverses into safeguards. | "stress test", "pre-mortem", "what could go wrong" |

### Writing (short-form prose, Voice Calibration v3.0)

| Skill | What It Does | Key Triggers |
|-------|-------------|-------------|
| **Email Writer** | Draft an email in your voice — subject, opener, body, close. Two-step draft-then-critique with baked-in Voice Block. | "draft an email to", "email to [name] about", "write an email" |
| **Memo Writer** | 1–3 page structured memos: decision docs, scope docs, strategy memos, position papers, recurring board / investor updates. Thesis-first. | "memo on", "decision doc", "scope doc", "strategy memo", "position paper", "board update", "monthly CEO letter" |

## Workspace Structure
```
Your Workspace/
├── _hq/                       # Headquarters
│   ├── MASTER_TRACKER.md      # Everything you're juggling
│   ├── BUSINESS_CONTEXT.md    # Who you are (Claude reads this first)
│   ├── DECISION_LOG.md        # Every major decision + rationale
│   ├── PEOPLE.md              # Relationship tracker
│   ├── BRAND_VOICE.md         # How you sound
│   ├── briefings/             # Daily briefing snapshots
│   ├── summaries/             # Weekly/monthly executive summaries
│   ├── audit-reports/         # Audit history
│   └── intel/                 # Knowledge base
├── _exploring/                # Early-stage ideas
├── _archive/                  # Completed/paused work
└── [Your Projects]/           # One folder per project
    ├── PROJECT_CONTEXT.md
    ├── SESSION_NOTES_[Name].md
    ├── ref/                   # Reference files
    └── meetings/              # Call prep briefs
```

## Requirements

**Python dependency.** Command Room generates `.docx` briefs (Call_Prep, Past_Meeting, etc.) using [python-docx](https://pypi.org/project/python-docx/). The currently-pinned version is **`python-docx==1.2.0`**.

If python-docx isn't already installed, the plugin will auto-install it the first time it generates a brief, and print a one-line notice to stderr so you know the install is happening. The install is idempotent and pinned — no surprise upgrades.

**Pre-install (recommended for locked-down environments).** Corporate networks that block PyPI egress should pre-install python-docx via your internal package mirror before first use:

```
pip install python-docx==1.2.0
```

**Declared dependencies.** The plugin manifest (`.claude-plugin/plugin.json`) declares its Python dependencies under the `python_dependencies` field. This is a Command Room–specific field (the Claude Code plugin schema doesn't currently parse it) — it exists for SBOM tooling and for ops teams that need a machine-readable dependency list. To update the pinned version, change both `python_dependencies` in `plugin.json` and `PYTHON_DOCX_PIN` in `shared/scripts/brief_writer.py`.

## Connectors

See CONNECTORS.md for the full list. Start with Email + Calendar for immediate impact. The more tools you connect, the better the briefings, meeting prep, and automatic updates become.

## How It Gets Better Over Time

- **Week 1:** Claude knows your projects and basic context
- **Week 3:** Real session history, knows your patterns and people
- **Month 2:** Knows every person, decision, and open issue across all projects
- **Month 3+:** Like a chief of staff who's been in every meeting and remembers everything

The more you use it, the better it gets. You don't do any extra work to make that happen.

## The Three Things That Matter

For the system to work, you need to do three things consistently:

1. **Start by loading context** — say "what's going on" for the full briefing, or "go [project]" to jump straight into a specific project. Either way, Claude loads everything it knows before you start working.
2. **End sessions with "end session"** — this saves everything and updates the tracker
3. **Process meetings after calls** — this is where the richest data enters the system

Everything else is enhancement. But these three habits are what make the system compound.
