---
name: automation-scanner
description: "Find the low-hanging automation wins hiding in the CEO's own workspace — repetitive work, manual data pulls, and copy-paste patterns across session notes and meeting transcripts — then rank by time-saved-vs-build-effort. Use when the CEO says 'what can be automated', 'automation scan', 'automation audit', 'show me automations', 'where am I wasting time', 'what should I automate first'. Produces a ranked list with scope notes and suggested build approach. DOES NOT fire on 'set up [specific tool]' (that's implementation, not scanning) or 'build me a [workflow]' (that's a design/build request — this skill identifies opportunities, doesn't build them)."
---

## Skill Boundary (v2.1)

- **Use automation-scanner for:** surveying the CEO's current workflows and surfacing ranked automation opportunities. Output is advisory — a prioritized list.
- **Does NOT build automations** — it identifies them. Once the CEO picks one, a specialized skill or custom build handles implementation.

## Writer Contract (v3.7.1+ — substrate-aware)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Primary writer for:**
- `_hq/audit-reports/automation-scan-[YYYY-MM-DD].docx` — ranked automation-opportunity report. Per CONTRACT Rule 27 (no .md deliverables); the report is `.docx` so it renders correctly in Word / Pages when the user opens it.

**Appends to:**
- `_hq/data/events.jsonl` — one `automation_opportunity_surfaced` event per ranked opportunity, with `{title, scope_summary, current_pattern_summary, estimated_time_saved_minutes_per_week, suggested_build_approach, rank, scan_report_path}`. Emitting per opportunity (not one event per scan) lets `scaffold-automation` reference a specific opportunity by `seq`, and lets `cleanup` track surfaced-but-not-scaffolded items over time ("you've surfaced 8 opportunities this quarter, scaffolded 2, deployed 1 — what's blocking the rest?").

**Reads from:**
- Session notes, meeting transcripts (the prose corpus for repetitive-work pattern detection).
- `_hq/data/events.jsonl` — for recurring patterns AND for prior `automation_opportunity_surfaced` events (so the same opportunity doesn't get re-surfaced every scan; dedup by `title`-similarity + scan-window comparison).
- `_hq/data/events.jsonl` `automation_deployed` events — so opportunities already shipped via `scaffold-automation` get filtered out of the next scan, not re-ranked.
- Local workspace file structure.

**Conflict boundary:** sole writer of `automation_opportunity_surfaced` events. The paired `scaffold-automation` skill (v3.8.0) is the sole writer of `automation_scaffolded` and `automation_deployed`.

**Why the upgrade (v3.7.1 note):** pre-v3.7.1 this skill was advisory-only — the scan produced a `.docx` report and nothing else. There was no way to know which opportunities had been scanned, which were scaffolded, and which were sitting on the floor. The per-opportunity event closes the loop and prepares the substrate for the v3.8.0 `scaffold-automation` pairing.

---

# Automation Opportunity Scanner

**For:** CEOs who want to identify the low-hanging fruit for building internal tools and workflows that save time.

## What It Does

Scans your workspace systematically to find patterns — repeated tasks, processes described in meetings, email patterns, recurring prep work — and surfaces concrete automation opportunities ranked by ROI (time saved per week) and build complexity.

Runs in two modes:
1. **CEO-facing mode** ("what can be automated?"): Shows opportunities during onboarding or as a scheduled scan. Creates wow moments. Builds trust in the Command Room system.
2. **Operator mode** (maintenance audit): Surfaces patterns that might not be obvious at first glance — repeated manual tasks, undocumented processes described in meetings, email templates that should exist, session history showing repeated manual requests.

## How to Use

```
"What can I automate?"
"Run an automation scan"
"Show me automation opportunities"
"What processes could we build?"
"Automation audit"
```

## What It Scans

### 1. Email Patterns
- Emails sent repeatedly to the same recipients with similar content (follow-ups, status updates, vendor requests, check-ins)
- Email templates that should exist but don't
- Responses to common questions that recur weekly/monthly
- Scheduling requests or calendar coordination patterns
- Finance/expense/invoice patterns
- External communication loops (customer updates, board reports, investor check-ins)

**Source:** Google Gmail — look for repeated message patterns over last 30-90 days.

### 2. Calendar Patterns
- Recurring prep work before meetings (prep time blocked, same meeting setup every week)
- Recurring post-meeting work (debrief, follow-up scheduling, note distribution)
- Calendar blocks for repetitive administrative work
- Meeting types that always need the same context pulled (all-hands, board calls, 1:1s, etc.)
- Scheduling bottlenecks (back-and-forth emails to find meeting times)

**Source:** Google Calendar — examine recurring events and blocks over last 30 days.

### 3. Task Tracker Patterns
- Tasks that reappear in the same format (weekly status update, monthly board report, recurring cleanup)
- Items with the same tags/labels appearing frequently
- Tasks that keep bouncing between "blocked" and "in progress"
- Recurring administrative work (expense reports, timesheets, check-ins)

**Source:** `_hq/data/events.jsonl` (Tier 1 canonical event log per `references/SOURCE_OF_TRUTH.md`) — read commitment events directly via `shared/scripts/cru_match.py::load_open_commitments` for the open set, plus a full scan for historical recurring patterns. Do NOT analyze MASTER_TRACKER.md as the source — it's a Tier 2 projection that regenerates at coarse cadence and would mask the month-over-month repetition signal automation-scanner needs.

### 4. Meeting Notes Patterns
- Processes described verbally in meetings that aren't in any system yet
- Action items that follow the same template repeatedly
- Decisions that need templates
- Onboarding or repetitive workflows mentioned in session notes
- Questions asked repeatedly in meetings

**Source:** SESSION_NOTES files in each project folder — scan for repeated action items, recurring questions, described workflows that should be systems.

### 5. Session History Patterns
- Things you ask M to do manually that could be skills (e.g., "prep me for every Monday meeting", "send weekly status to board", "pull metrics for our dashboard")
- Requests that follow the same pattern weekly/monthly
- Context you assemble manually that could be an automated dashboard or report
- Manual data entry or copy-paste work

**Source:** Read this session's recent history and check the workspace for README or logs of recurring requests.

## Output Format

**Rendering (v3.13.8+ — Bug #53):** render the `.docx` via the canonical `shared/scripts/brief_writer.py` `make_brief(brief_kind="automation_scan", ...)`. Do NOT hand-roll python-docx or use docx-js. brief_writer enforces canonical typography, Heading 1/2/3 hierarchy, and runs the universal post-render leak scanner (Bug #57/#59/#54) automatically. Use the v3.13.8 `table` primitive for the ranked-list section rather than synthesizing bullets for column-shaped data.

Ranked list with time-saved estimate, build complexity, and one-line description. Use this structure:

```
Here's what I found you could automate — [DATE]
Found [N] opportunities worth looking at.

#1 — saves about [TIME_SAVED] a week
What it does: [What the automation does]
Build size: [Small / Medium / Large]
Where I saw it: [Email pattern / Calendar pattern / Task pattern / Meeting notes / Session history]
Time to build: [e.g., "2-4 hours to build the skill + template"]
Example from your workspace: [Concrete example]

#2 — saves about [TIME_SAVED] a week
[same format]

---
Quick wins (do these first):
1. [Small build + high time savings]
2. [Next]

Bigger builds, bigger payoff:
1. [Medium build]

Long-term — these change how you work:
1. [Large build]

What's next:
- Pick ONE quick win to build this week
- I'll help you build it
- Run it for 2 weeks, then we'll check how much time it actually saved
```

### Complexity Definitions

- **S (Small):** Template + simple Gmail or Slack integration. Can build in 1-2 hours. Examples: email template, weekly email reminder, Slack checklist.
- **M (Medium):** Skill with data pulls from 2-3 sources (Calendar + Gmail, or Task tracker + project context). Conditional logic. Build in 3-6 hours. Examples: weekly status digest, meeting prep brief, automated report.
- **L (Large):** Multi-step workflow, connects 4+ sources, requires custom logic. Build in 8+ hours. Examples: full onboarding workflow, automated dashboard with live data pulls.

### ROI Calculation

Time saved = (minutes per occurrence) × (frequency per week)

Examples:
- Email you send 3x/week that takes 8 minutes = 24 minutes/week = "24 min/week"
- Weekly meeting prep that takes 15 minutes = "15 min/week"
- Monthly report that takes 2 hours = "30 min/week" (2 hours ÷ 4 weeks)

## Triggers

- "what can be automated"
- "automation opportunities"
- "show me what I could automate"
- "automation scan"
- "automation audit"
- "what processes could we build"

## Connected Tools

- **Google Gmail** — Search for repeated email patterns (follow-ups, status updates, vendor requests, check-ins)
- **Google Calendar** — Analyze recurring meetings, prep blocks, post-meeting work
- **Slack** — Scan for repeated questions or administrative patterns (optional)
- **Granola** — Review meeting notes for undocumented processes
- **`_hq/data/events.jsonl`** — Analyze commitment and interaction patterns for repetition (canonical Tier 1 source per `references/SOURCE_OF_TRUTH.md`; NOT MASTER_TRACKER.md, which projects at coarse cadence)
- **SESSION_NOTES files** — Search project notes for processes described verbally
- **PROJECT_CONTEXT.md** — Extract workflow details

## Gotchas

### "But we already tried automation and it didn't work"
**What happens**: You see an opportunity but remember a previous tool that didn't stick.
**Instead**: Ask: "Why didn't it work?" Common reasons: tool was too slow, wasn't integrated into your flow, required extra steps. This time, build it as a skill that lives in your Command Room — it will feel integrated because it IS.

### Opportunity is too vague
**What happens**: Scanner surfaces "meeting prep could be faster" without specifics.
**Instead**: Always provide a concrete example from the workspace. "Every Monday you spend 15 minutes pulling context on the CEO roundtable. You ask for attendee background, check past notes, and search for relevant emails. A skill could do this in 10 seconds."

### Email/Calendar patterns that are false positives
**What happens**: You sent 3 emails to the same person but they're all different (not a real pattern).
**Instead**: Only flag if there's a genuine template hiding in the pattern. Look for: same recipient + similar subject + similar structure.

### Task tracker patterns aren't visible without context
**What happens**: Scanning MASTER_TRACKER shows "status update" tasks but they're from different projects (not really a pattern).
**Instead**: Only flag if the same task appears repeatedly in the SAME project or across multiple projects (suggesting a system-wide process).

### Session notes contain aspirational patterns ("we should automate X")
**What happens**: Session notes say "we need a better way to track this" but no one is actually doing it manually.
**Instead**: Focus on things that ARE happening now. "You should automate X" is great for future builds, but actual automation opportunities are things you're already doing manually.

### Skipping connected sources
**What happens**: Gmail or Calendar isn't connected, so the scan returns fewer opportunities.
**Instead**: Skip gracefully. Build the scan from whatever sources ARE connected. If only local files (SESSION_NOTES, MASTER_TRACKER) are available, scan those deeply. Say at the end: "Note: Gmail and Calendar aren't connected. If they were, I'd likely find 3-5 more email/meeting prep opportunities."

### Opportunity requires systems you don't have yet
**What happens**: "You should automate expense reports" but you don't have an expense tracking system.
**Instead**: Flag it but mark it lower priority. "This would require first building an expense tracker" — include it in suggestions but note the dependency.

### Measurement is hard
**What happens**: You estimate "30 min/week saved" but after building, it only saves 10 minutes.
**Instead**: Always suggest measuring. "After you build this, run it for 2 weeks and track actual time saved. Adjust the estimate."

## Mode Details

### CEO-Facing Mode ("What can I automate?")

**Trigger**: User asks "what can be automated?" during onboarding or as a standalone scan.

**Output**: Shorter, more digestible. Focus on wow moments. Highlight 3-5 quick wins and 1-2 medium-complexity ideas.

**Tone**: "Here's where I see opportunity to reclaim time. Let's start with the quick wins."

**Next step**: Ask "Want me to build the first one?" and use skill-creator to build it immediately.

### Operator Mode (Audit)

**Trigger**: M is running a periodic maintenance audit. Scans systematically for patterns that aren't obvious.

**Output**: Full detailed report. Include everything, ranked by ROI. Provide concrete examples with dates and specifics.

**Tone**: "Here are the patterns I'm seeing. Some of these might be worth discussing with the CEO."

**Next step**: "Want me to present the top 3 quick wins to the CEO, or build one directly?"

## Rules

- **Be specific.** Every opportunity needs a concrete example from the workspace (with dates if possible).
- **Measure everything.** Always estimate time saved per week. If you can't, it's probably not a real opportunity.
- **Prioritize ruthlessly.** Show quick wins first (S complexity + high ROI). Don't overwhelm with long-term infrastructure.
- **Consider effort, not just impact.** A 10-minute/week opportunity with S complexity is better than a 60-minute/week opportunity with L complexity (for momentum).
- **Skip connected sources gracefully.** If Gmail isn't connected, don't say "can't scan emails" — just note it at the end.
- **Don't guess.** Opportunities must be based on actual patterns in the workspace, not assumptions.

## What It Doesn't Do

- It doesn't build the automation (use **skill-creator** for that)
- It doesn't estimate cost or resourcing
- It doesn't prioritize based on company strategy (you do that)
- It doesn't track which automations were built or their actual ROI (that's for a future Automation Tracker)

## Next Steps

After the scan:
- Pick ONE quick win (S complexity)
- Use **skill-creator** to build it
- Test it for 2 weeks
- Measure actual time saved
- Repeat
