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

**"what's going on"** — Your daily briefing. Open the Claude app, say this, and know where everything stands. Checks email, calendar, and Slack for updates since your last session.

**"go [project]"** — Jump into a specific project. Loads all context — session notes, project docs, tracker status — and checks connected sources for anything new related to that project.

**"end session"** — Say this when you're done working. Saves everything, updates the tracker, carries forward open items. This is the save button — nothing persists without it.

**"new project [name]"** — Create a new project. Asks discovery questions, creates the folder and context docs, adds it to the tracker. Also works with "new exploring [name]" for early-stage ideas.

**"meeting notes"** — Paste a transcript or let it pull from Granola. Decisions, action items, people, and commitments cascade into the right places automatically.

**"list projects"** — Full roster of what you're working on, with canonical names and aliases. The "wait, what did I call that?" lookup.

**"prep call with [person]"** — Generates a meeting brief pulling from calendar, email, Slack, past notes, and commitments. Walk into any meeting prepared.

## Skills

Every skill, what it does and what to say to start it: [SKILLS.md](../SKILLS.md). That list is generated from each skill's own description, so it cannot drift.

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
    ├── SESSION_NOTES_<folder name>.md
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

**Declared dependencies.** The plugin's Python dependencies are listed, pinned, in `requirements.txt` at the plugin root (for SBOM tooling and ops teams that need a machine-readable dependency list). They used to sit in `plugin.json` under `python_dependencies`, but the plugin sync strips unknown manifest keys and warned on every sync. To update the pinned version, change both `requirements.txt` and `PYTHON_DOCX_PIN` in `shared/scripts/brief_writer.py`.

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
