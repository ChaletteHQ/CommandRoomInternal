# Workspace Manager — Detailed Reference

This file contains lookup tables, detailed procedures, and examples that support the core SKILL.md. The skill reads this file during execution as needed.

---

## Master Tracker Full Template

The MASTER_TRACKER.md file lives at `[WORKSPACE_ROOT]/_hq/MASTER_TRACKER.md` and is the single source of truth across the entire workspace.

```markdown
# Master Tracker
> Last updated: [date] by [trigger]

## Active (Stage 3+)
| Project | Category | Stage | Last Touched | Next Action | Waiting On | Priority |
|---------|----------|-------|--------------|-------------|-----------|----------|

## Scoping (Stage 2)
| Project | Category | Stage | Last Touched | Next Action | Waiting On |
|---------|----------|-------|--------------|-------------|-----------|

## Exploring (Stage 1)
| Project | Category | Started | Last Touched | What's Interesting | Next Step |
|---------|----------|---------|--------------|-------------------|-----------|

## Inbox (Stage 0)
| Item | Category | Added | Note |
|------|----------|-------|------|

## Steady State (Stage 4)
| Project | Category | Stage | Last Touched | Status | Next Check-In |
|---------|----------|-------|--------------|--------|----------------|

## Recently Archived
| Project | Category | Archived | Reason |
|---------|----------|----------|--------|

## Commitment Tracking
| Commitment | Project | Owner | Date Made | Due By | Status |
|------------|---------|-------|-----------|--------|--------|

## Staleness Rules
| Category | Yellow flag | Red flag |
|----------|------------|----------|
```

**Key points:**
- "Last Touched" is the date of the last session that touched this project (YYYY-MM-DD)
- "Waiting On" captures blockers or dependencies — things preventing forward progress
- "Next Action" is always the *very next thing* to do, not a vague goal
- "Staleness Rules" should define when projects turn yellow (no activity for X days) or red (no activity for Y days)
- The full tracker is already used by the onboarding skill's templates.md. If you need the exact markdown boilerplate, reference that file.

---

## Source Check Procedures

### Gmail Search

When checking for new emails, construct searches by combining (these reads are **name-lookup orientation only** per `references/SOURCE_OF_TRUTH.md` — used to build the search intent, not to determine "is this thread outstanding"):

- **From contacts:** sender names / emails from PEOPLE.md (Tier 2 view, fine for name lookup)
- **By project:** project name / key terms from MASTER_TRACKER.md (Tier 2 view, fine for name lookup)
- **By date:** a "since" floor (the `after` intent key, compiled per provider by `connector_adapters/mail.py`)
- **By status:** for "is this blocker resolved" decisions, derive from `_hq/data/events.jsonl` — find `commitment_resolved` / `thread_resolved` events scoped to the relevant thread. The "Waiting On" row in MASTER_TRACKER is the search-term source; the resolution decision derives from events.jsonl per the canonical reader `cru_match.load_open_commitments`.

**Example search intent:** `{"any_of": [{"from": "rae@example.com"}, {"subject": "Acme"}], "after": "2026-04-08"}` — compiled per provider by `connector_adapters/mail.py::compile_search`; never a hardcoded operator string.

Keep results concise. If there are multiple emails, group by sender and summarize the thread in 1-2 lines. Don't quote full email bodies — just note: "Skyler sent the vendor quotes" or "Skyler's waiting on your feedback."

### Calendar Check

Pull events from today + next 7 days:
- Identify projects each meeting relates to
- Note attendees (cross-reference with PEOPLE.md)
- Flag if there are back-to-back meetings without prep time
- Check if any meetings relate to "Waiting On" items in the tracker

Present as: "You have 3 meetings this week: [Project A] call on Wed (Skyler), [Project B] standup on Thu (team), and [Project C] kickoff on Fri (stakeholders)."

### Slack Check

Search project-related channels for:
- Recent messages (last 48 hours) mentioning tracked project names
- Unread threads in relevant channels
- Mentions of the user or direct messages
- Follow-ups to earlier conversations

Keep it brief: "Unread thread in #northstar about vendor setup" or "Skyler's been messaging about Q2 planning in #strategy."

### Google Drive Check

Search for recently modified docs:
- Filter by date (since last session note)
- Filter by project name or team folder
- Note the document type (proposal, research, etc.)
- Track who modified it and when

Present as: "Updated proposal for [Project A] (Skyler, 1 hour ago)" or "Research doc on [Project B] (shared with you yesterday)."

### Granola Check

Look for unprocessed transcripts:
- Search by date (transcripts created since last session)
- Note meeting title and attendees
- Count how many are pending processing
- When found during "end session," offer: "You have [X] unprocessed meetings — want me to handle those now or next session?"

Never auto-process. The user might want to listen to them first or batch them.

---

## Trigger Routing Table

The Command Room plugin has 14 skills. When the user's request could trigger multiple skills, use this routing table:

| User says | Route to | Not |
|-----------|---------|-----|
| "prep me for dinner with [person]" | **people-crm** (relationship prep) | call-prep |
| "prep me for my 2pm meeting" / "prep call with [person]" | **call-prep** (meeting brief) | workspace-manager |
| "audit" / "quick audit" / "how's the workspace" | **cleanup** `--quick` mode | workspace-manager |
| "cleanup" / "full audit" / "deep audit" | **cleanup** `--full` mode (comprehensive) | workspace-manager |
| "morning briefing" / "daily briefing" / "brief me" | **morning-briefing** (proactive digest) | workspace-manager |
| "what's going on" / "workspace status" | **workspace-manager** (daily briefing) | cleanup |
| "what needs attention" | **workspace-manager** (briefing) | cleanup |
| "what changed since my briefing" / "quick update" | **workspace-manager** (delta mode) | full briefing |
| "recap" / "what happened this week" / "weekly summary" / "monthly recap" | **cleanup** `--summary` mode (absorbed legacy `executive-summary`) | workspace-manager |
| "who is [person]" / "who do I know at [company]" | **people-crm** | general knowledge |
| "process meeting" / "meeting notes" | **meeting-notes** | workspace-manager |
| "log decision" / "what did we decide about" | **decision-log** | workspace-manager |
| "intel review" / pasted URL or link | **intel-intake** | workspace-manager |
| "scan my files" / "process local files" / "what's on my desktop" / "ingest folder [path]" | **workspace-ingest** (folder-mode, v2.14.20+ — absorbed the former `context-ingestion`) | workspace-manager |
| "check for updates" / "what changed in the plugin" / "run update check" | **command-room-update-bridge** (absorbed legacy `update-check`) | workspace-manager |
| "how is [person] doing" / "team status" / "[person]'s commitments" / "prep 1:1 with [person]" | **team-intelligence** | people-crm |

**When overlapping:**
- If workspace-manager is already active (e.g., during "go [project]") and the user asks for meeting prep, handle it inline. Don't try to switch to call-prep mid-flow.
- The routing table is a tie-breaker when multiple skills could apply. Use the user's exact language as a guide.

---

## Strategic Advisor Mode — Examples

Strategic Advisor Mode is about noticing patterns in the workspace and offering grounded observations. Here are examples of what this looks like:

### Pattern Recognition
- "You've been in reactive mode on [Project A] for 3 weeks — every session is crisis management. Is there a proactive step you could take?"
- "You're juggling 5 active projects. Three of them haven't been touched in a week. Is that expected, or are some getting starved?"

### Cross-Project Connections
- "What you learned about vendor management in [Project A] might apply to the [Project B] setup. Want to revisit that?"
- "Both [Project A] and [Project B] are waiting on the same stakeholder. Could you batch those conversations?"

### Time/Attention Imbalance
- "You spent 2 hours on [Project A] today and 10 minutes on [Project B], but [Project B] is the higher priority. Does that match your plan?"
- "You haven't opened [Project C] in 4 days. Is it on pause, or did it slip?"

### Challenging Assumptions
- "You said [X] was critical, but your time allocation doesn't reflect that. Do we need to adjust priorities, or did something change?"
- "You're waiting on Skyler for [Project A], but Skyler has been quiet for a week. Have you followed up, or is that on hold intentionally?"

### What NOT to do:
- Don't offer vague, generic advice ("You seem busy" or "Work-life balance is important")
- Don't make observations without data (no "You might want to..." if you don't know the situation)
- Don't give business strategy advice outside your role as a workspace manager
- Don't flag something as a "gotcha" if it's clearly intentional

---

## Implicit Project Detection — Detailed Rules

When the user works on something without explicitly saying "go [project]," detect and confirm before loading context.

### How to detect:
1. Parse the user's request for project names, client names, or keywords from MASTER_TRACKER.md
2. Fuzzy match against existing project folders and tracker rows
3. If confidence is high (exact name match or very close), ask for confirmation

### Ask format:
> "That sounds like it's related to your [Project Name] project — want me to pull up the context?"

One sentence only. Don't explain why or what you're about to load — just ask.

### What "yes" means:
Run the full "go [project]" flow:
- Load PROJECT_CONTEXT.md
- Load SESSION_NOTES
- Load MASTER_TRACKER row
- Check connected sources (Gmail, Slack, Drive)
- Present status + next actions
- Then help with the original request using full context

### What "no" means:
Help with the task directly without context loading. Don't be pushy or suggest they "should" load context.

### Edge cases:
- **Multi-project mention:** "I need to email Skyler about both Northstar and the new partnership." Detect that there are two projects. Ask: "I see you're touching Northstar and the new partnership — want me to pull up both contexts?" Or if one is clearly the main focus, ask about that one and offer to load the other if needed.
- **Passing mention:** If the user says "This is like what we did for [Project A]" but the main work is something else, don't ask. Use judgment.
- **Repeat in same session:** If you already confirmed "go [project]" this session, don't re-ask for subsequent work on that project.
- **Tentative reference:** If the user says "I might work on [Project]" but hasn't started, don't ask yet. Wait until they actually start working on it.

---

## Gotchas — Deep Dive

### Session Notes Cumulative Rule

When appending to SESSION_NOTES during "end session," **never drop open items from previous sessions.**

**Right approach:**
1. Read the previous session notes (usually the most recent few entries)
2. Identify ALL open items (listed explicitly or implicit in "next action")
3. Carry them forward into the new session entry
4. Note which ones are still pending and which ones progressed

**Example:**
```
Previous session: "Open items: Email Skyler about timeline, get approval from Skyler, draft proposal"
This session: We emailed Skyler (resolved), draft is 50% done (in progress), Skyler hasn't responded yet (still waiting)
New entry should carry: "Open items: Finish proposal draft, waiting on Skyler for approval"
```

The cumulative approach prevents "old" items from disappearing from the notes just because the current session didn't touch them.

### Auto-Update Trap

When source checks surface new information (an email from Skyler, a Slack message, etc.), **never auto-update the tracker.** Always present to the user first.

Why: An email from Skyler doesn't necessarily mean a task is done. Skyler might be asking a question, sending a draft for feedback, or sharing partial results. Confirmation is required.

**Right approach:**
1. "I found an email from Skyler with vendor quotes. Want me to update the tracker to mark that resolved?"
2. User confirms (or corrects)
3. Only then update MASTER_TRACKER.md

### Staleness Thresholds

During briefings and status checks, flag projects that haven't been touched:
- **Yellow flag:** No activity for ~5-7 days (depends on the project's natural cadence)
- **Red flag:** No activity for ~14+ days

**But:** Check the tracker's "Staleness Rules" section for the user's preferred thresholds. Some projects are intentionally on pause or slow-moving (e.g., "waiting for regulatory approval" — 30 days is normal). Don't flag things that are clearly in a waiting/holding pattern.

### Source Check Speed

The user expects briefings to be snappy, not comprehensive. A full "what's going on" should take 2-3 minutes to read, not 10+.

**Speed rules:**
- Don't read entire email threads — just note the key point
- Don't pull every email from a contact — only the recent relevant ones
- Don't scroll through 3 months of Slack history — just check the last 48 hours
- Summarize: "3 emails from Skyler about the proposal" not "Skyler sent email 1 with outline, email 2 with questions, email 3 with..."

If there's a lot of activity, note that and ask if the user wants details: "There's been a lot of movement on Northstar this week — want me to dig into it, or just hit the highlights?"

### Granola Processing in End Session

When "end session" finds unprocessed transcripts:
- **Offer, don't assume:** "You have 2 unprocessed meetings — want me to handle those now, or next session?"
- **Respect the user's choice.** Some users want to listen first. Some want to batch them. Don't auto-process.
- **If the user says "yes," run the meeting-notes skill** to process the transcript.
- **If the user says "next session," just note it in the briefing log** so it's not lost.

---

## Troubleshooting

### "I can't find the project"

**Check in this order:**
1. Exact name match in MASTER_TRACKER.md
2. Exact folder name in `[WORKSPACE_ROOT]/`
3. Fuzzy match (similar names, old names, variations)
4. Check `_archive/` for archived projects
5. Ask the user: "I don't see a project called '[name]' — is it under a different name, or should I create it?"

### "Source check found something but I'm not sure if it's relevant"

Present it to the user with context: "I found an email from [person] about [topic]. Does that relate to [Project]?" Let them decide if it's relevant.

### "The tracker is out of sync with reality"

This usually happens when:
- End session wasn't run (user closes without saving)
- A decision was made outside Cowork without updating the tracker
- A project moved to a different stage but wasn't updated

In your next "what's going on," ask clarifying questions: "The tracker says you're waiting on [X], but I heard you say [X] is done — want me to update that?" or "When did [Project] move to [stage]?"

### "The user is working fast and we're falling behind"

If the user is actively working and asking lots of follow-up questions mid-flow:
- **Don't stop to save after every change.** Batch updates until "end session."
- **Keep notes of what's changing in your context** so you don't lose it.
- **Warn before end session:** "I've tracked [X] changes this session — ready to save?"

---

## Brain Update Procedures

When updating PROJECT_BRAIN.md during "end session," follow these section-by-section rules:

**People:** Add anyone new mentioned this session (name, role, notes). Update existing entries if you learned something new (communication preference, relationship change). Don't duplicate — check if they're already listed.

**Gotchas:** Add any gotcha discovered this session — something that went wrong, a sensitivity uncovered, a pattern to avoid. Format: what happens → why → what to do instead. These are permanent — never remove gotchas.

**Active Threads:** Refresh the table — add new threads, update status of existing ones (waiting → resolved, new next steps). Mark threads as "Resolved" with the date when they're fully closed. Resolved threads stay in the active table for 30 days (per maintenance Rule 2), then get compressed to one-liners in Thread History.

**Custom Workflows:** If a recurring pattern was discussed or executed for the second+ time, capture it as a workflow (e.g., "Weekly check-in with Aria: Monday email summary of all projects"). Don't add first-time processes — wait for repetition.

**Key Context:** Add any significant context that would change how you approach this project next time — pivotal moments, strategic shifts, new constraints, relationship dynamics. Keep each entry to 1-2 lines.

**Trigger Aliases:** If the user referred to this project by a non-standard name, add it. (e.g., "go Sam's thing" = CEO Search)

**General rules:** Append, don't overwrite. Keep entries concise (1-2 lines each). If nothing changed for a section, leave it alone. The brain should grow steadily, not get rewritten every session.

---

## Delta-Based Session Briefings (v1.7.0+)

Delta mode enables lightweight, efficient briefings by showing only what changed since the last briefing checkpoint. This feature is triggered when the user asks "what changed since my briefing" or uses equivalent language, and a checkpoint exists from within the last 8 hours.

### Session Checkpoint

At the end of every "what's going on" briefing (after displaying status to the user), write a lightweight checkpoint JSON file to `_hq/.checkpoints/[TIMESTAMP].json`.

**Checkpoint timestamp format:** ISO 8601 with timezone (e.g., `2026-04-14T14:30:00-07:00`). Use this as the filename.

**Checkpoint contents:**
```json
{
  "timestamp": "2026-04-14T14:30:00-07:00",
  "briefing_type": "full",
  "projects": [
    {
      "name": "Project A",
      "last_modified": "2026-04-14T14:15:00-07:00"
    },
    {
      "name": "Project B",
      "last_modified": "2026-04-12T10:30:00-07:00"
    }
  ],
  "items_surfaced": [
    {
      "type": "email",
      "sender": "Skyler",
      "subject": "Vendor quotes",
      "project": "Project A",
      "timestamp": "2026-04-14T13:00:00-07:00"
    },
    {
      "type": "slack",
      "channel": "#ops",
      "summary": "Timeline change",
      "timestamp": "2026-04-14T11:45:00-07:00"
    }
  ],
  "commitments_shown": [
    {
      "text": "Send spec",
      "project": "Project A",
      "due_date": "2026-04-14T12:00:00-07:00",
      "status": "overdue"
    }
  ],
  "team_interactions": [
    {
      "person": "Skyler",
      "interaction_type": "email",
      "timestamp": "2026-04-14T13:00:00-07:00"
    },
    {
      "person": "Skyler",
      "interaction_type": "slack",
      "timestamp": "2026-04-14T11:30:00-07:00"
    }
  ]
}
```

### Delta Mode Trigger

When the user says "what's going on" and a checkpoint exists from within the last 8 hours, automatically switch to delta mode.

**Opening message:** "Showing what changed since your [TIME] briefing."

Example: "Showing what changed since your 9:15 AM briefing."

### Delta Scan

In delta mode, only check:

**Connectors:**
- Gmail: New emails since checkpoint timestamp (filter by date, sender, project keyword)
- Slack: New messages in tracked channels since checkpoint timestamp
- Calendar: New events added since checkpoint timestamp
- Drive: Documents modified since checkpoint timestamp

**Projects:**
- Only load MASTER_TRACKER rows for projects whose files were modified since checkpoint
- Skip projects with no activity since the checkpoint

**Commitments:**
- Only surface commitments that are **newly overdue** (crossed the due date since checkpoint)
- Only surface commitments that are **newly delivered** (marked complete since checkpoint)
- Skip commitments already flagged in the previous briefing

**Team:**
- Only log new interactions (email, Slack, meeting, call) since checkpoint timestamp
- Skip team members who had no new activity since checkpoint

### Delta Output Format

Present changes in a clear, scannable format:

```
SINCE YOUR 9:15 AM BRIEFING:

New: [email from Skyler re: vendor quotes — relates to Project A]
New: [Slack message in #ops about timeline change]
Changed: [Project B session notes updated — you worked on it at 10:30]
Due now: [Commitment to send spec — was due at noon]

Everything else: unchanged from this morning.
```

**Structure:**
- **New:** Newly surfaced items (emails, Slack messages, calendar events) with brief context
- **Changed:** Projects or docs that were modified (note when and by whom)
- **Due now:** Commitments that just crossed their due date
- **Everything else:** One-liner confirming that other projects, people, and commitments remain unchanged

Keep the summary tight — 4-8 lines maximum. If there's significant activity on a project, offer: "There's been movement on [Project A] — want the full context, or just the highlights?"

### Full Briefing Override

When the user says "full briefing" or "what's going on — everything," bypass delta mode and run the standard full briefing (same as before v1.7.0).

The override disables delta detection for this briefing only. The next checkpoint is still written after the full briefing completes.

### Checkpoint Cleanup

**Auto-deletion rule:** Checkpoints older than 24 hours are automatically deleted during the next briefing run (no need to manually clean them up).

**Max per day:** Keep a maximum of 5 checkpoints per calendar day. When a 6th checkpoint would be created on the same day, delete the oldest checkpoint from that day before creating the new one.

**Cleanup timing:** Check and clean during the "what's going on" trigger, before writing the new checkpoint.

### Implementation Notes

- Delta mode is a read-only feature — it never modifies the tracker, projects, or commitments. All updates still happen during "end session."
- If a checkpoint file is corrupted or missing, fall back to a standard full briefing and log the issue.
- The checkpoint directory (`_hq/.checkpoints/`) is created automatically if it doesn't exist.
- Checkpoint cleanup and creation are idempotent — running the same briefing twice in quick succession (e.g., user refresh) won't create duplicate checkpoints.

---

## Team Profile Update Procedures

When updating `_people/` person files during "end session" or "what's going on," follow these field-by-field rules:

**Interaction Log:** Append one entry per meaningful interaction found. Format: date, type (meeting/email/slack/session/call), one-line summary, source reference. Only log CEO-relevant interactions — "meeting moved to 3pm" is not relevant, "Aspen Project deal is falling through" is. Keep 10 entries in the active log; when adding #11, move the oldest to Previous Interactions.

**Active Commitments:** For new commitments: add with commitment text, due date, source (which meeting/email), status "Open." For delivered commitments: update the status cell to "Delivered" with today's date IN THIS MARKDOWN TABLE ONLY — the canonical closure is `commitment_state.close_commitment(...)` on events.jsonl (Stage B); **NEVER edit the commitment event's `data.status` in events.jsonl (F4 — in-place mutation is the forbidden write class; closure = appended tombstone only)**. For overdue commitments: update status to "Overdue" (automatic — any commitment past due date; the projector computes overdue from the EFFECTIVE due, so a deferred commitment is not overdue). Never remove commitments — they compress to Commitment History per maintenance Rule 3.

**Cross-Project Presence:** Refresh by scanning all PROJECT_BRAIN.md People tables for this person's name. Update the table with current project, role/involvement, and status. This is auto-generated — the CEO doesn't edit it.

**Working Style:** Only update when substantive new information is learned — a communication preference, a behavior pattern, a working dynamic. Don't add trivial observations.

**Flags:** Only add when the CEO explicitly asks or when something clearly needs surfacing next interaction. Flags are temporary — prompt to clear after 14 days or 3+ sessions.

**Non-roster mentions:** If a person was mentioned in session work but is NOT on the team roster, don't prompt during "end session" (too disruptive to the save flow). Note it internally — the team skill catches it on next "team status" or the audit flags it.

