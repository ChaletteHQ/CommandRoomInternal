# Onboarding Details & Reference Material

This file contains procedures, templates, examples, and calibration guides extracted from SKILL.md. The main skill reads this file when it needs deep context.

---

## v3.4.1 onboarding-v2 deltas (2026-05-17 — post-M-testing iteration)

Two changes on top of the v2 compression:

1. **Step 1c absorbed Step 1d + Step 1e.** All four setup questions (role / day-to-day note / email exclusions / timezone) now render as ONE chat-action widget via `widget_mode: "onboarding_setup"`. Pre-v3.4.1 they were three sequential markdown-list prompts (1c, 1d, 1e). Step 1 now has 3 sub-beats (1a-1c).
2. **Schedules trigger moved from Step 4c → Step 2c2.** Operator opens Chat A (`set up command room schedules`) at the start of the scan instead of waiting until after the workspace build. Schedules registration needs only Step 1's data (timezone + workspace.first_go_months) — no value blocking on Step 2's scan or Step 4's build. Step 4c now recommends ONLY Workspace Map (Chat B).

The Voice Block / Orientation Beats / Specific Finding Fallback Chain / Relationship Card Template / Guided First Run sections below are unchanged from v2.

---

## v2 Step Numbering Redirect (onboarding-v2 / 2026-05-17)

This file was written against the pre-v2 8-step structure (Steps 0-7). Onboarding-v2 compressed to 5 steps. When this file references "Step Xa" below, translate using this map:

| Pre-v2 reference | onboarding-v2 location | Notes |
|---|---|---|
| Step 0a (workspace guard) | **Step 1a** | preserved, renumbered |
| Step 0b (auto-detect intro) | **Step 1b** | preserved, renumbered |
| Step 0c (workspace shape) | **Step 1c** (widget item 1) | REWRITTEN with role-first wording (6 options + Other). v3.4.1+ delivered as item 1 of the Step 1 setup widget. |
| Step 1 (Scan) — overall | **Step 2 (Scan)** | renumbered |
| Step 1a (start scan / connector inventory) | **Step 2a + 2b** | inventory in 2a, extraction in 2b |
| Step 1b (orientation beats) | **Step 2c** | preserved, renumbered |
| Step 1c (analyze + classify) | **Step 2d** | classification now SILENT (no in-chat confirms) |
| Step 1d (one specific finding) | **Step 2e** | preserved |
| Step 2 (Reveal) — overall | **Step 3 (Reveal compressed)** | renumbered + compressed |
| Step 2a (open with finding) | **Step 3a** | preserved |
| Step 2b (org tree confirmation) | **Step 3b** | confirmation prompts REMOVED — tree shown silently |
| Step 2c (project reveal) | **Step 3c** | correction prompts REMOVED |
| Step 2d (voice profile + before/after) + Step 4c (voice draft) | **Step 3d** | MERGED into three-way prompt-AND-output contrast; Output 3 richness is data-injected (named cross-refs from events.jsonl) |
| Step 2e (relationship card) | CUT from Step 3 — moves to operator-opened Chat 3c (`tell me about [person]`) |
| Step 2f (save profile) | **Step 3e** | preserved |
| Step 3 (Gap questions) — overall | DISTRIBUTED | content cut OR redistributed |
| Step 3a (prompt restructuring) | DROPPED | default all workspaces to Yes-path CLAUDE.md template |
| Step 3b (Test Me) | CUT — moves to Chat 3+ where it works against richer data |
| Step 3c (email exclusions) | **Step 1c** (widget item 3) | v3.4.1+ delivered as item 3 of the Step 1 setup widget. |
| Step 3d (timezone) | **Step 1c** (widget item 4) | v3.4.1+ delivered as item 4 of the Step 1 setup widget. |
| Step 4 (Build workspace) — overall | **Step 4 (Build workspace files)** | renumbered |
| Step 4a (build files) | **Step 4a** | preserved |
| Step 4b (show tracker) | **Step 4b** | preserved |
| Step 4c (voice draft) | MERGED into Step 3d's three-way contrast |
| Step 4d (sidebar-dashboards no-op disclaimer) | REMOVED — dashboards install via operator-opened parallel chats |
| New Step 2c2 (operator notify — schedules) | NEW v3.4.1+ | cue to open Chat A (`set up command room schedules`) at the start of the scan, in parallel with Step 2's connector reads |
| New Step 4c (operator notify — workspace map) | NEW v2 / SLIMMED v3.4.1+ | cue to open Chat B (`install workspace map`) in parallel. Pre-v3.4.1 this beat ALSO mentioned Chat A schedules — that moved to Step 2c2. |
| New Step 4d (Quick Commands install) | NEW v2 | silent install — moved from previously-planned end-of-Step-2 because person_001 isn't in entities.json until Step 4a |
| Step 5 (Live briefing) — overall | CUT — moves to operator-opened Chat 3a (`weekly recap`) for substantive output |
| Step 5a (commitment backfill) | RELOCATED — runs automatically as side-effect of weekly-recap skill (Chat 3a) |
| Step 5b (run briefing) | CUT — Chat 3a does this with 7d of context, not today's |
| Step 5c (project deep-dive) | CUT — operator-opened Chat 3b (`go [Project A]`) does this |
| Step 5d (person deep-dive) | CUT — operator-opened Chat 3c (`tell me about [Person A]`) does this |
| Step 6 (Action close) | CUT — duplicates Chat 3's job |
| Step 7 (Guided run + handoff) — overall | **Step 5 (Built summary + handoff seed)** | restructured |
| Step 7a (guided two-prompt run) | CUT — operator-opened Chats 3a/b/c do this with richer data |
| Step 7b (operator-handled scheduled tasks) | REPLACED by **Step 2c2 operator notify** (v3.4.1+ — operator opens Chat A in parallel with Step 2's scan, not at end of Step 4) |
| Step 7c (personalized handoff 4-beat) | REPLACED by **Step 5a (built summary) + 5b (deep-dive candidates)** |
| Step 7d (companion plugin recommendations) | CUT from v2 scope |
| Step 7e (final checkpoint + plugin_install event) | **Step 5c** | preserved |

**Template content below remains useful** — orientation beat scripts, voice contrast template, voice draft template, relationship card template, finding fallback chain, team detection procedures — even where the surrounding step labels are stale. Use the redirect table above to translate references. The Voice Contrast Template and Voice Draft Template specifically MERGE in v2; both their content blocks now apply to **Step 3d** (the new three-way prompt-AND-output contrast).

---

## The Pitch (Full Scripts)

Use these to deliver the three-layer value proposition. Pick the language that matches the user's style.

### Layer 1: "Claude will actually know you"

**For technical users:**
"Right now, every time you open Cowork, Claude starts fresh — no persistent memory of your business, projects, or preferences. After setup, Claude becomes a personalized system that maintains context across sessions. You explain once, and it knows permanently. No more re-explaining scope, priorities, or history."

**For non-technical users:**
"Right now, every conversation with Claude starts from zero. You tell Claude who you are, what you're working on, what happened last time. After we set this up, Claude will know your business, your projects, your people, and how you like to work. You never have to explain again."

**For business leaders:**
"Every tool becomes more valuable when it knows your business. After setup, Claude understands your strategic context, your key relationships, your decision-making process, and your priorities. It's like having a strategic advisor who's been in every meeting."

### Layer 2: "Everything you're juggling, tracked automatically"

**For busy operators:**
"The Master Tracker captures all your active work, ideas you're exploring, and everything you've committed to. The system learns from your emails, calendar, and meetings. After a month, Claude knows your work ecosystem better than most people you work with. After 20 sessions, it's like having a chief of staff."

**For detail-oriented people:**
"Every project gets a context document that grows with every session. Decisions, commitments, people, status — it all accumulates in one place. Your email, calendar, and meeting transcripts feed into these docs automatically. One input, everywhere updates."

**For teams:**
"Your team can reference the same source of truth. Decisions made, commitments captured, project status — all available to anyone who needs context on your work."

### Layer 3: "Nothing falls through the cracks"

**For high-accountability people:**
"The system tracks every commitment you make — with dates, who you made it to, and status. cleanups flag what's slipping, what's overdue, and what's at risk. You get proactive nudges instead of surprises."

**For leaders managing multiple projects:**
"Staleness detection works per-category. High-stakes projects get flagged quickly if they go quiet. Ideas can sit for a month without pressure. The system knows what "stale" means in YOUR context."

**For the organized:**
"cleanups run automatically. The system scans everything, scores your health, and offers to fix the easy stuff. You're never managing a growing backlog of loose threads."

---

## Smart Detection Details

### Step 1-2: Tool Scanning Procedures

#### Gmail Scan (if connected)

Try to:
1. Pull last 30 sent emails (use gmail_search_messages with "from:me" and sort by date descending)
2. Extract and group:
   - Frequent recipients (name + email, group by frequency: daily, weekly, occasional)
   - Recurring topics/subjects (watch for project names, initiative names, recurring patterns)
   - Project names mentioned (anything that looks like a work item or initiative)
   - Communication patterns (tone, length, structure — are they concise? detailed? diplomatic? direct?)
3. Optional: If 10+ emails exist, capture communication style in a short summary (max 3 sentences)

If this succeeds, you have HIGH confidence on contacts and communication style (if email count >= 10). MEDIUM if < 10 emails.

#### Calendar Scan (if connected)

Try to:
1. Pull 14 days back + 14 days forward (use gcal_list_events for this time range)
2. Extract and group:
   - Recurring meetings (these are ongoing workstreams — watch for the pattern)
   - Unique attendees (who's on their calendar? which names appear most?)
   - Meeting titles that suggest projects (watch for project names, client names, initiative codes)
   - Upcoming commitments (any deadlines or time-sensitive items in the title?)
3. Note patterns:
   - Which days are heavy meeting days?
   - Types of meetings: 1:1s, team meetings, external calls?
   - Any obvious groupings (client calls vs. internal vs. personal)?

If you find 3+ recurring meetings or a clear pattern of workstreams, you have HIGH confidence on projects. MEDIUM if 1-2 patterns. LOW if calendar is sparse.

#### Slack Scan (if connected)

Try to:
1. Search channels they're active in (use slack_search_channels with a broad query or list active channels they're in)
2. Extract:
   - Active channels (these map to projects/teams — which ones do they post in most?)
   - Frequent conversation partners (who do they work with in Slack?)
   - Recent topics (what's being discussed in their active channels?)

If you find 3+ active channels with clear purpose, you have HIGH confidence on projects. MEDIUM if 1-2. LOW if Slack isn't used or they're in few channels.

#### Google Drive Scan (if connected)

Try to:
1. Search recently modified files (last 30 days) (use google_drive_search or list recent files)
2. Extract:
   - Active document names (what are they working on?)
   - Folder structure (do they already have organized folders per project or per client?)
   - Project-related files (proposals, contracts, spreadsheets — what do they indicate?)

Note: If they already have organized folders, those become strong project folder candidates for the workspace.

If you find 3+ documents with clear project association, you have HIGH confidence. MEDIUM if 1-2. LOW if Drive is sparse.

#### Granola Scan (if connected)

Try to:
1. List recent meeting transcripts (use granola tools if available)
2. Extract:
   - Who they met with (attendees)
   - Topics discussed (what are the meetings about?)
   - Any action items or decisions mentioned
   - Frequency of meetings (are there recurring meeting patterns?)

If you have 5+ recent transcripts, you have HIGH confidence on people and projects. MEDIUM if 2-4. LOW if < 2 or no transcripts.

#### Folder Scan (always available)

Try to:
1. List top-level directories and files in mounted folder
2. Flag anything that looks like a project, client, or workstream
   - Named folders that suggest organization (Projects, Clients, Initiatives, etc.)
   - Existing organizational patterns (per-client folders? per-project? per-team?)
   - Any documents that give hints at their structure
3. If they have clearly organized subfolders, those become candidates for how to structure the workspace

If you find 3+ organized subfolders or clear naming conventions, you have HIGH confidence on how they organize. MEDIUM if some structure but unclear. LOW if folder is unorganized or new.

---

## Confidence Assessment

Use this table to categorize what you learned from scans:

| Topic | What you need | High Confidence | Medium Confidence | Low Confidence |
|-------|--------------|-----------------|-------------------|----------------|
| **Projects/workstreams** | Names, status, what each one is | Found in 2+ sources OR clearly labeled folder with distinct subfolders | Found in 1 source only | Not found or ambiguous |
| **Key people** | Names, roles, which projects they connect to | Appeared in multiple emails/meetings to same person | One source only (e.g., just email or just calendar) | Not found |
| **Categories/groupings** | How their work clusters (clients vs. internal vs. exploring) | Obvious from data: email groupings, folder groupings, project types stand out | Some pattern visible but not obvious | Can't tell from data alone |
| **Priorities/stakes** | Which projects matter most (revenue risk, client relationships) | Email frequency + meeting density make it obvious which projects are high-focus | Some signal but unclear | Can't tell from data alone |
| **Communication style** | How they write and talk (tone, formality, pace) | 10+ sent emails to analyze — clear patterns visible | A few emails or transcripts — some patterns | No writing samples or transcripts |
| **Commitments** | Open promises, deadlines, deliverables | Explicit in emails/calendar (deadlines, "committed to" language, "by [date]") | Implied in meeting titles or project descriptions | Not visible in any source |

---

## Block 2 Calibration

### Priority/Stakes Question

When asking "Which of these are high-stakes," help them think through:

- **Client/revenue work:** Dropping the ball = client upset, money lost, relationship damage
- **Personal business:** Dropping the ball = revenue impact or core operation interrupted
- **Partnerships:** Dropping the ball = relationship damage or missed opportunity
- **Ideas/exploring:** Dropping the ball = idea dies (which might be fine)
- **Internal admin:** Dropping the ball = inconvenience but not critical

### Staleness Rules Default Table

If they're unsure, use these sensible defaults:

| Category | Yellow Flag | Red Flag | Reasoning |
|----------|-----------|----------|-----------|
| Client work / active deals | 5 days | 10 days | High stakes — quick escalation |
| Core projects / own business | 7 days | 14 days | Moderate urgency — watch but not panic |
| Partnerships / collaborations | 10 days | 21 days | Lower urgency — relationship maintenance |
| Ideas / exploring | 14 days | 30 days | Very low urgency — ideas can marinate |
| Inbox items (random tasks) | 30 days | 60 days | Lowest urgency — nudge to archive if still sitting |

Explain the difference:
- **Yellow flag:** Something hasn't been touched in X days. System surfaces it in your briefing. "Hey, [Project] hasn't been touched in 8 days — want to check in?"
- **Red flag:** Something has been stale for too long. System marks it as at-risk in the audit. "Warning: [Project] is 15 days stale. Recommend prioritizing."

---

## Block 3 Tool Options & Explanations

When asking about tools, use this guide to explain what each connector unlocks:

### Gmail
**What it does:** Claude reads your email threads, searches your history, can draft responses using actual context.
**What it unlocks:** 
- "What did [person] say about [topic]?" — Claude searches your email history
- Draft capabilities — Claude references actual communications when writing emails
- Contact context — Claude learns who your frequent contacts are
**When to recommend:** Always. Even if they don't use email much, it's useful for drafting and context.

### Google Calendar
**What it does:** Claude sees what's coming, who's on your calls, what you've booked, recurring commitments.
**What it unlocks:**
- Meeting prep — 10-second briefings before any call
- Commitment visibility — Claude knows what you've agreed to be at
- Pattern recognition — Claude sees if you're overbooked or have gaps
**When to recommend:** Always. Even if they use calendar lightly, meeting prep alone is worth it.

### Slack
**What it does:** Claude searches your channel history, reads conversations, understands your team communication.
**What it unlocks:**
- Team context — Claude knows what your team is discussing
- "What did the team say about [topic]?" queries
- Team briefings — Claude can summarize Slack activity
**When to recommend:** If they use Slack actively. If they work solo, it's lower priority.

### Google Drive
**What it does:** Claude searches and reads your docs — proposals, contracts, notes, specs, everything.
**What it unlocks:**
- Document retrieval — "Find that document about X"
- Proposal/contract context — Claude references actual docs when working on related tasks
- Proposal drafting — Claude knows your actual scope and terms
**When to recommend:** If they work with documents (proposals, contracts, specs). Essential for sales/business development.

### Granola
**What it does:** Auto-pulls your meeting transcripts, turns them into structured meeting notes automatically.
**What it unlocks:**
- Zero-friction transcript capture — transcripts feed the system without manual work
- Automatic action item extraction — decisions and commitments are extracted from transcripts
- Meeting intelligence — Claude learns from every meeting automatically
**When to recommend:** If they have frequent meetings (3+ per week). Transforms meeting data into system intelligence automatically.

### DocuSign
**What it does:** Claude tracks signature requests, documents pending signature, status of signed agreements.
**What it unlocks:**
- Agreement awareness — Claude knows what's waiting for signature
- Signature routing — Claude can prepare docs for signature via DocuSign
- Contract tracking — Claude maintains agreement status
**When to recommend:** If they sign contracts or agreements regularly. Legal teams especially.

### Other Tools to Mention
If they ask about other tools they use (Jira, GitHub, HubSpot, Asana, etc.), note them for future connector development. For now, focus on the six core connectors above.

---

## Block 5 Calibration

### System Engagement Level

Help them understand the difference:

**Quiet (Passive)**
- Claude maintains files and answers when asked
- Minimal proactive questions or observations
- Useful if: You want the system to organize without being pushy, or you prefer to direct all interactions
- Drawback: You have to remember to ask. Easy to miss things

**Medium (Conversational)**
- Claude surfaces 1-2 observations per interaction, only when grounded in real data
- "I noticed [Project] hasn't been touched in 10 days — want to update it?" style interactions
- Useful if: You want a thinking partner but not an aggressive nudge system
- This is the most popular choice

**Loud (Active Thinking Partner)**
- Claude asks 2-3 questions per interaction
- Proactively flags issues, connects dots between projects, challenges priorities
- "You've got 3 high-priority items due Friday. How are we sequencing these?" style interactions
- Useful if: You want an aggressive advisor who pushes back and helps you think through priorities
- Drawback: Requires you to engage more. Can feel pushy if you prefer autonomy

### Health Check Delivery

Ask where they want the cleanup to land:

**Option 1: Ready in Cowork when you open it**
- Pro: You see it immediately when you start your session
- Con: It's one more thing in your feed

**Option 2: Sent to Slack**
- Pro: It shows up in your Slack flow where you're already checking messages
- Con: Requires Slack to be connected. Not visible if you don't open Slack that day

**Option 3: Scheduled for a specific day/time**
- Pro: You know exactly when to expect it. Can schedule around it
- Con: It's one more calendar item or reminder

Most people choose Option 1 or 3. Option 2 is great if they live in Slack.

---

## Connector Details

See "Block 3 Tool Options & Explanations" above for full connector context.

The key principle: Don't force all connectors at once. Let the user choose their pace. Some people want everything connected immediately. Others want to start with email and calendar, then add Slack and Granola after a week. Respect their preference.

---

## Brand Voice Template

When you capture their voice, structure it like this:

```markdown
# Your Brand Voice
> Learned from [X] emails and [Y] transcripts
> Last Updated: [date]

## How You Sound
[3-5 key characteristics with brief examples. Examples:
- "Direct and concise — you get to the point in 1-2 sentences"
- "Data-driven — you back up claims with numbers"
- "Warm but professional — you use people's names and remember details"
- "Skeptical of hype — you ask hard questions before committing"]

## Specific Patterns

### Opening Style
[How they typically start emails/messages. Example: "I usually open with context or a question, not pleasantries"]

### Closing Style
[How they typically end. Example: "I close with clear next steps or an explicit ask"]

### Common Phrases & Patterns
[Things they actually say. Examples:
- "Let me be clear about X..."
- "The numbers show us..."
- "What's your thinking on...?"
- "We should [action] by [date] because [reason]"]

### Tone by Audience
[If they shift tone based on who they're talking to, note it. Example:
- With clients: More formal, lots of context-setting
- With team: More direct, assumes shared context
- With executives: Numbers-first, brief]

### Decision Language
[How they frame choices and decisions. Example:
- "I usually frame options as 'X is stronger because Y,' not as 'pros and cons'"
- "I decide based on impact and timeline, not feelings"]

## What NOT to Do
[Things that would sound out of character. Examples:
- Don't use corporate jargon ("leverage," "synergy," "alignment")
- Don't be overly casual (they're professional)
- Don't hedge excessively ("I think maybe possibly..." — they're more decisive)]

## Examples

### Good (sounds like them):
"I've been thinking about how we handle vendor negotiations. Here's what I'm seeing: the current process takes 6 weeks and we're losing deals. I'd recommend moving to a 3-week timeline with clear decision gates. It's a change, but the data supports it. When can we talk through the details?"

### Bad (doesn't sound like them):
"We should really focus on leveraging our vendor relationships to potentially achieve some synergistic outcomes that could tangentially benefit our overall strategic alignment going forward."
```

---

## Default Smart Detection Rules

When Step 1 scan scanning completes, use these rules to decide which path to take:

**Path A triggers (run full setup, skip Step 1):**
- Found 3+ high-confidence discovery topics
- Have clear project list from 2+ sources
- Have clear people/contacts list
- Have clear categories/groupings
- Can confidently draft initial MASTER_TRACKER and PROJECT_CONTEXT files

**Path B triggers (run partial Step 1):**
- Found 1-2 high-confidence discovery topics
- Can only fill in some blocks of the tracker
- Missing key information (e.g., have projects but not priorities, have people but not roles)
- Will need 5-10 targeted questions to fill the gaps

**Path C triggers (run full Step 1):**
- Found 0 high-confidence topics
- Folder is new/empty
- No tools connected
- No email/calendar/Slack history to draw from
- Will need full discovery interview

---

## Team Discovery Details

### Step 1: Detect team members from existing data

Silently scan what you already have from Step 1 scan + Step 2:

- **Calendar:** Look for recurring 1:1 meetings. People on recurring meetings are almost certainly direct reports or key people. Extract: names, frequency, meeting titles.
- **Gmail:** Look for the CEO's most-emailed people (last 30 days). Cross-reference with calendar contacts.
- **Slack:** Look for DM channels or frequent @mentions.
- **Project brains:** Scan all PROJECT_BRAIN.md People tables created in Step 2. Extract names appearing across multiple projects.
- **PEOPLE.md:** Check the contact database built during Step 2 for anyone with leadership-sounding roles.

### Step 2: Present and confirm

> "Based on your calendar and emails, it looks like you have regular 1:1s with [Name 1], [Name 2], and [Name 3]. Are these your direct reports or key people you'd like me to track?"

Present in a simple table:
```
| Name | How I Found Them | Recurring Meeting? | Projects They Touch |
```

Ask:
- "Are these the right people? Anyone missing?"
- "Anyone here who is NOT a direct report but still important to track?" (board member, key vendor, partner)
- "Anyone I should NOT track?" (admin, notetaker)

### Step 3: Create _people/ folder and profiles

1. Create `[WORKSPACE_ROOT]/_people/`
2. Create `_team-config.md` from template (in team-intelligence skill's reference files)
3. For each confirmed team member, create `_people/[name].md`:
   - Pre-populate **Identity** from what you know (name, role, email from calendar)
   - Pre-populate **Interaction Log** with last 3-5 touchpoints found in email/calendar/Granola
   - Pre-populate **Cross-Project Presence** from project brains
   - Pre-populate **Active Commitments** if action items were found in recent meetings
   - Leave **Working Style** and **Flags** empty — those come from the CEO's experience
4. Add all members to `_team-config.md` roster

### Step 4: Explain the value

> "I've set up profiles for [X] people on your team. Here's what this gives you:
> - Say **'prep me for my 1:1 with [Name]'** before any meeting — I'll pull open commitments, recent activity, and flags.
> - Say **'my team'** for an overview — who's on track, who has overdue items, who you haven't talked to.
> - Profiles update automatically from meetings and sessions. You don't maintain them.
> - This is your private view. Nobody else sees these files."

### Step 5: Capture working style (quick round, optional)

For each person, ask ONE question:
> "Any working style notes I should know about [Name]? How they communicate, what motivates them, any quirks? Even one sentence helps."

If the CEO doesn't want to do this now, skip. Profiles fill in naturally over time.

---

## Orientation Beats (v2.4)

These are the spoken beats Claude delivers during Step 1 while the scan runs in the background. Total floor: 90 seconds of core beats (1–3). Extension beats (4–7) stack every ~20 seconds if scan runs longer. Never narrate scan volume. Never invent new beats past Beat 7.

### Core Beats (first 90 seconds)

**Beat 1 (0–30s) — What it does:**
> *"While I'm scanning, here's what this actually does. It keeps track of everyone and everything you're juggling — deals, people, commitments, the stuff you'd forget if no one reminded you. It doesn't replace your calendar or inbox. It sits on top of them and makes sure nothing slips."*

**Beat 2 (30–60s) — Privacy:**
> *"Everything stays in the folder you chose. Nothing leaves your machine unless you send it somewhere. There's no cloud database behind this — the files are yours, you can open them without me, and deleting the folder deletes me."*

**Beat 3 (60–90s) — How memory works:**
> *"Two things make this work. Say 'end session' when you're done — that's the save button. Then keep coming back. The first session is fine. The fiftieth feels like someone who's been in every meeting this year."*

### Extension Beats (90s+ — stack every ~20s)

**Beat 4 (90–110s) — Customization:**
> *"I watch how you work and adjust. If I'm too chatty, tell me. Too quiet, tell me. If I use a word wrong — 'client' when you say 'account' — tell me once. I don't forget."*

**Beat 5 (110–130s) — Compounding advantage:**
> *"The more you use this, the further it pulls from generic Claude. Month one is fine. Month six feels like talking to someone who's been in every meeting you've had this year."*

**Beat 6 (130–150s) — Honest limits:**
> *"This isn't magic. It'll miss things. When it does, correct me and I learn. Never fight it — just tell me what I got wrong."*

**Beat 7 (150–170s) — Adjustable cadence:**
> *"You can run this as chatty as a co-pilot or as quiet as a filing cabinet. Default is 'polite interrupter' — I'll surface things once, then shut up. Change it anytime."*

**After 170s:** idle on Beat 7's tail. Say something like *"almost done — just finishing the last pass"* and wait. Never fabricate new beats.

---

## Specific Finding Fallback Chain (v2.7.22, was "Surprise Fallback Chain")

The one specific finding for Step 2a's opener is mandatory. Run the ordered search below and take the FIRST real, specific, actionable hit. Do not search further.

**Search order:**

1. **Missed commitment** — Scan sent email for phrases like "I'll send", "let me get you", "by [date]", "will follow up". Cross-reference against follow-ups in subsequent sent messages or completed drive documents. Flag if no follow-through exists and original promise was ≥5 days ago.

2. **Cold relationship** — Identify contacts with prior weekly cadence (3+ interactions in 60-day span) who have been silent for ≥30 days. Pick the highest-weight contact (most total interactions).

3. **Overdue follow-up** — Scan inbox for unanswered incoming messages from important people (top 20% by frequency). Flag any with last incoming message ≥7 days old and no reply.

4. **Calendar anomaly** — Look for: conflicting meetings, high-stakes meeting (board/external) with no prep materials visible, three-meetings-deep day with no buffer, or first meeting of tomorrow with no agenda.

5. **Pattern they didn't flag** — Recurring themes across 3+ threads. Examples: same objection from multiple clients, same question from multiple team members, same delay mentioned by multiple vendors.

6. **Oldest unresponded thread from important person** — Last resort. Pick single oldest unresponded inbound from a top-20-by-frequency contact.

**If all six return nothing (rare):** substitute the clean-inbox line from SKILL.md. Never fabricate specificity.

**Format rule:** the finding must include a real name, a real date, and a real actionable nudge. Generic findings like "you have some open commitments" are worthless — they degrade the moment and make the whole flow feel like guessing.

---

## Voice Contrast Template (v2.4)

Step 2c prove-the-voice beat. Pick a generic CEO task (e.g., follow-up on a proposal, thank-you note, request for an intro) and render it two ways.

**Generation rules:**

- **Generic version:** use classic LLM hedging — "I wanted to follow up", "just checking in", "please let me know if you have any questions or concerns." Passive, wordy, no character.
- **Your Claude version:** pull 3–5 stylistic features from `_hq/BRAND_VOICE.md`: opening habit, closing habit, sentence length median, one signature phrase, and things-they-don't-do (e.g., never uses exclamation points). Write a sentence using those exact features.
- **Length match:** both sentences ≈ same word count so contrast is about style, not brevity.

**Pattern:**

```
Generic Claude would write:
  "[stilted LLM version]"

Your Claude writes:
  "[CEO-voiced version with their actual openings/closings/signature moves]"
```

Close with:
> *"That's not a template. That's your voice — openings, closings, rhythm, length, what you DON'T do. Every email, every update, every draft uses this from here on."*

**v2.14.23+ THREE-BRANCH LOCK** — see `command-room-onboarding/SKILL.md` Step 2d for the canonical decision tree. Summary:
- 10+ sent emails: full profile + contrast (this template).
- 3-9 sent emails: same profile + contrast, opened with thin-data note ("I have a small sample to work with…"). Use whatever sample exists; don't invent.
- <3 sent emails: explicit defer message; skip the contrast for now ("I don't have enough yet — I'll calibrate after you write through me a session or two").

Partial fires (profile yes, contrast no, when data is borderline) are FORBIDDEN. Choose a branch, fire it fully.

---

## Relationship Card Template (v2.4)

Step 2d prove-the-memory beat. Pick the most-connected person from scan (highest total interaction count across email + calendar + Slack).

**Card fields (all must be real — never invent):**

```
[FIRST LAST] · [Role, Org]
Last touch: [N days ago] — [one-line context of the last interaction]
Owed: [one-line commitment status — "nothing open" is a valid value]
Recent context: [one-line summary of last substantive exchange]
Pattern: [cadence observation — "weekly Tuesday 1:1, never missed" or "sporadic but dense bursts"]
Notes: [one specific detail — a kid's name, a project they mentioned, a hobby — ONLY if it's actually in the data]
```

**Rules:**
- Never guess the role/org if it's not in an email signature or meeting title.
- The "Notes" line is optional. Leave blank rather than invent.
- If the candidate doesn't have enough real context for 4+ fields, fall back to the next-most-connected person.
- **v2.14.23+: NEVER skip this beat silently.** If nobody has 4+ fields, render a labeled partial card with whatever 1-3 fields ARE populated, prefaced with: *"Your relationship layer is still thin from the scan — here's what I have on [Name], partial picture. As we work together, this will fill in."* The wow moment for thin-data users is "look, I'm starting to track these people for you" — not a polished card. Surface the partial.

Close with:
> *"I'll do this for anyone you ask about — 'tell me about [name]' pulls up their card."*

---

## Voice Draft Template (v2.4)

Step 4 post-build beat. Select a real thread from the scan and draft a reply in the CEO's voice.

**Selection rules (in order — first match wins):**

1. Open thread from an important person (top 20% by frequency) with last inbound ≥3 days old and no reply from the CEO.
2. Open thread with a soft deadline mentioned in the inbound message ("by end of week", "when you get a chance").
3. Incoming thank-you or intro where a short reply is overdue.
4. **v2.14.23+: if no candidate in the top three categories, NEVER skip silently.** Surface an explicit "nothing needs reply right now" message instead:

   > *"I checked your inbox — nothing in there needs a reply right now. That's actually a good sign; I'll surface drafts when something's overdue or has a soft deadline."*

   Do NOT draft a reply to a thread that doesn't need one. Do NOT silently move on without saying anything. The customer needs to know the system looked and found nothing actionable — not that the system forgot to look.

**Draft rules:**

- Use real context from the inbound message — reference what they actually said.
- Pull tone, rhythm, openings, closings, and signature moves from `_hq/BRAND_VOICE.md`.
- Match length to the CEO's median reply length (from BRAND_VOICE).
- Never include placeholder text ("[your name]", "[insert X]"). Fill everything or leave it blank.
- Do NOT send. Do NOT queue. Just draft and present.

**Delivery pattern:**

> *"While we're here — you've got an open thread with [Name] from [X days ago]. I drafted a reply in your voice. Read it. If it sounds like you, you can send it as-is. If not, tell me what to fix and I'll learn."*
>
> [full draft, quoted inline]
>
> *"If that's not quite right, tell me what to change."*

---

## Guided First Run (v2.7.22)

Step 7a closes onboarding with **two** interactive prompts — `prep for [meeting]` first, then a `go [project]` → add-something → `end session` loop second. Order matters: call prep is lower-stakes / immediate output; the go-and-save loop is the muscle-memory moment that everything downstream depends on. Each prompt is presented in a code block so the CEO can read and type it back. If Cowork exposes clickable prompt suggestions, attach those as well — but always show the text.

### Prompt 1 — Call Prep

- **Primary command:** `prep for [next meeting name]`
- **Selection rule (in order):**
  1. Next calendar meeting in 72 hours with ≥1 attendee.
  2. Most-recent past meeting in the last 14 days that has NOT yet been processed (no `meeting_processed` event in events.jsonl). The brief still has a real target — and the next bridge-out can pivot to "want to process it now?".
  3. `prep call with [most-connected person]` against the highest-frequency contact from the scan.
- **Underlying skill:** `call-prep`.
- **Bridge out:** *"Nice. That's how every call starts from here. One more — the most important habit to lock in."*

### Prompt 2 — Go-and-save loop (`go [project]` + add something + `end session`)

ONE loop, three phases. Teaches the CEO that `go` loads the project's full context and `end session` locks in whatever they did during the session — including a one-line addition the CEO types in the middle.

**Selection rule for the project (in order):**
1. **Most-active project** — highest event count in last 14 days. The user has the most context loaded mentally about this one, so adding a one-line update is natural.
2. **Project the user explicitly mentioned in Step 3 gap questions** (if you captured one).
3. **First project alphabetically** as the last-resort fallback. Never use a placeholder; always name a real project.

**Phase A — Load (CEO types):**

- **Command:** `go [project name]`
- **Underlying skill:** `workspace-manager` project-load.
- **What to say after context loads:** *"Good. Now your full picture of [project] is in context. Add one thing — anything you're thinking about it. A next step you're planning, a concern that's nagging you, a decision you're chewing on. One line is enough — what would you add?"*

**Phase B — Add (CEO writes free-form):**

Wait for the CEO to type their addition. Accept it as-is — don't reformat, don't second-guess, don't suggest improvements. Common shapes: a sentence about a decision they're weighing, a one-line status update, a concern about timing, a name they want to remember to follow up with.

If the CEO replies with a question instead, apply Detour-Return Protocol — answer briefly, return: *"Got it. One line you'd add about [project] — anything you're thinking?"*

Acknowledge the addition in a single short sentence (don't expand it into a side task). Then move to Phase C.

**Phase C — Save (CEO types):**

- **Command:** `end session`
- **Underlying skill:** `workspace-manager` end-session flow.
- **What to say after end-session runs:** one-line confirmation closing the loop:

  > *"Saved your note ('[user's actual addition, ≤60 chars]') to SESSION_NOTES_[Project].md. Next session starts with everything you just put in place — including this. That's how the system compounds: you say what you're thinking, end session locks it in, you come back and it's all there."*

**Why this loop matters:** the entire system depends on the user understanding that `go` loads and `end session` saves. Reading about it doesn't stick; doing it once does. This is the single highest-leverage muscle-memory moment in onboarding. Do not skip it. Do not let the CEO talk past it. If they try, gently re-anchor: *"Quick — type `end session` first so we lock in what you just added. Then we wrap."*

### Post-loop: write FIRST_WEEK.md

After Phase C lands, save session notes for this run; write `FIRST_WEEK.md` to workspace root listing the two prompts the CEO just learned, plus 3 additional commands to try across their first week:
- `what's going on` — daily briefing across all projects (say it first thing each morning).
- `meeting notes` — paste a transcript or let Granola pull it; cascades to tracker / decisions / people.
- `cleanup` — Monday morning health check across the whole workspace.

**No bridge out after Phase C** — Step 7b's personalized handoff comes next.

### Pacing Rules

- **Wait for the CEO to actually type the prompt.** Do not auto-run on their behalf — the muscle memory is the whole point.
- **If the CEO skips or derails** (asks a question instead of typing the prompt), answer the question, then re-present the next prompt. Never force the sequence.
- **If the CEO types something close but imperfect** (e.g., `prep [meeting]` without "for"), accept it — the point is muscle memory, not pedantry.
- **Total expected time:** 3–5 minutes for both prompts including the user's addition in Phase B. If the CEO writes a long addition, accept it; if they hesitate, prompt once with examples then move on.

---

## Cut prompts (v2.7.17 and earlier — not in current guided run)

These prompts ran in earlier versions of Step 7's guided run but are no longer part of the live demo. They live in `FIRST_WEEK.md` instead, which the CEO discovers post-onboarding.

**Cut in v2.7.18:**

- **Dynamic prompt** — generated from scan signals (e.g., `show me who's gone cold`, `summarize where [project] stands`, `tell me about [commitment]`, `draft a reply to [person]`, `show me my week`). The "because" line referencing specific data was mandatory.

**Cut in v2.7.22:**

- **Daily briefing prompt** (`what's going on`) — was the v2.7.18-v2.7.21 first prompt. v2.7.22 dropped it from the live run because the go-and-save loop is more important muscle memory; `what's going on` is now in FIRST_WEEK.md as a daily habit and surfaces in Step 7b's personalized handoff line ("Open this back up tomorrow morning and say `what's going on`...").

If a future release re-expands the guided run, restore these from this section + the v2.7.17 / v2.7.21 history in CHANGELOG.md.

The standalone `Load a project` and `End session` prompts that ran in v2.7.17 and earlier are NOT cut — they're folded into v2.7.22's Prompt 2 (the go-and-save loop) which combines `go [project]` + add + `end session` into a single muscle-memory exercise. v2.7.21 and earlier had `end session` as week-one homework via FIRST_WEEK.md; v2.7.22 promotes it to the live run because doing it once with the user's own data is dramatically more sticky than reading about it later.
