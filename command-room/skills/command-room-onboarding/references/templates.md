# Workspace Templates

These templates are used during onboarding to scaffold the user's workspace. Replace all placeholders with real content from the discovery interview.

---

## BUSINESS_CONTEXT.md

This file is Claude's permanent reference about who the user is. It's read at the start of every session. Content grows over time as you learn more about their business, but the structure stays the same.

```markdown
# [Name/Company] — Business Context
> Last Updated: [date]

## Who You Are
(What they do, in THEIR words — not a corporate bio)

## What You're Working On
(Summary of projects and priorities from Block 1)

## Categories & How You Think
(The groups from Block 2 — client work vs. own projects vs. exploring, etc.)

## Tools & Integrations
(From Block 3 — which tools are connected and what they enable)

## How You Work
(Preferences from Block 4 and 5 — communication style, engagement level, decision-making)

## Brand Voice
(If captured — how to sound like them in draft work. Otherwise: "Not yet configured")

## Staleness Rules
(From Block 2 — what "stale" means for each category)

## Notes for Claude
- [Specific things about how to work with this person]
- [What they value — speed? thoroughness? brevity? creativity?]
- [Anything they want flagged for attention]
- [Their priorities and how they stack]
```

---

## MASTER_TRACKER.md

The brain. One file that shows everything at a glance. Seed with ALL data from discovery. Use THEIR categories and language.

```markdown
# Master Tracker
> Last updated: [date] by onboarding

## Active (Stage 3+)
| Project | Category | Stage | Last Touched | Next Action | Waiting On | Priority |
|---------|----------|-------|-------------|-------------|------------|----------|
[Seeded from discovery]

## Scoping (Stage 2)
| Project | Category | Stage | Last Touched | Next Action | Waiting On |
|---------|----------|-------|-------------|-------------|------------|

## Exploring (Stage 1)
| Project | Category | Started | Last Touched | What's Interesting | Next Step |
|---------|----------|---------|-------------|-------------------|-----------|
[Seeded from discovery]

## Inbox (Stage 0)
| Item | Category | Added | Note |
|------|----------|-------|------|

## Steady State (Stage 4)
| Project | Category | Stage | Last Touched | Status | Next Check-In |
|---------|----------|-------|-------------|--------|--------------|

## Recently Archived
| Project | Category | Archived | Reason |
|---------|----------|----------|--------|

## Commitment Tracking
| Commitment | Project | Owner | Date Made | Due By | Status |
|------------|---------|-------|-----------|--------|--------|

## Staleness Rules
| Category | Yellow Flag | Red Flag |
|----------|-----------|----------|
[From discovery — defaults if not specified:]
| Active projects | 5 days | 10 days |
| Own businesses | 7 days | 14 days |
| Exploring | 14 days | 30 days |
| Inbox | 30 days | 60 days |
```

---

## PROJECT_CONTEXT.md (one per active project)

Every active project gets one with REAL content. If you don't have enough info for a section, leave it out — don't write "[TBD]".

```markdown
# [Project Name] — Context
> Status: [Active/Exploring/Steady State]
> Category: [from discovery]
> Last Updated: [date]

## What This Is
(One paragraph from what they told you)

## Key People
(Anyone mentioned — name, role, relevance)

## Current State
(Where things stand right now)

## Open Items
(Anything that needs doing)
```

---

## SESSION_NOTES_[NAME].md (one per project)

**CRITICAL:** `[NAME]` = the user's first name. Set this once during onboarding and use it identically for every project. Example: if the user's name is Pat, every project gets `SESSION_NOTES_Pat.md`. This consistency is essential — every skill looks for this exact file name.

One per project, seeded with onboarding context:

```markdown
# [Project Name] — Session Notes

## Current Status (as of [date])
(One line from discovery)

## Active Work Items
- [From discovery]

## Open Questions
- [From discovery]

## Session Log

### [date] — Initial Setup
- Project created during command room onboarding
- Initial context: [what they said about this project]
- Next action: [from discovery]
```

---

## _exploring/[name]/notes.md

Lightweight notes for each exploring item:

```markdown
# [Idea Name]
> Stage: Exploring
> Added: [date]
> Category: [from discovery]

## What's Interesting
(Why they mentioned it)

## What's Known So Far
(Any details from discovery)

## Next Step
(What would move this forward)
```

---

## DECISION_LOG.md

Create this in `_hq/` during onboarding — even if empty. This is core infrastructure that other skills write to.

```markdown
# Decision Log
> Last Updated: [date]

## How to Use
Say "log decision" to add entries. Meeting notes also auto-cascade decisions here.

## Decisions
(None yet — decisions will appear here as you work)
```

---

## PEOPLE.md

Create this in `_hq/` during onboarding. Seed with any people mentioned during discovery.

```markdown
# People
> Last Updated: [date]

## How to Use
Say "who is [person]?" to look someone up. Meeting notes auto-add new contacts here.

## Contacts

(If people were mentioned during discovery, add them immediately with this structure:)

### [Person Name]
- **Company:** [company]
- **Role:** [role]
- **Relationship:** [how the user knows them]
- **Last Interaction:** [date or "onboarding"]
- **Notes:** [anything relevant from discovery]
```

---

## intel/INDEX.md

Simple knowledge base structure for content they capture:

```markdown
# Intel Index
> Last Updated: [date]

## Topics
- [Auto-populated as they add insights]

## By Source
- Email insights
- Meeting insights
- Articles/content
```

---

## BRAND_VOICE.md (if brand voice captured)

For detailed guidance on synthesizing voice from emails and transcripts, see **onboarding-detail.md** → "Brand Voice Template". Fill every section that the sent-email corpus supports — a thin corpus fills fewer sections, but never leave a section as a placeholder. The calibration is only as good as its specificity: exact greeting/sign-off strings, real measured numbers, and verbatim example sentences beat adjectives.

```markdown
# Your Brand Voice
> Learned from [X] sent emails and [Y] transcripts, [date range]
> Last Updated: [date] · Confidence: [high | medium | thin]

## How You Sound (the one-paragraph read)
[3–4 sentences that a stranger could use to impersonate them. Name the register
(direct/diplomatic, formal/conversational, warm/clinical), the density (terse vs.
expansive), and the single most identifying habit.]

## Signature Traits (ranked, most identifying first)
1. [Trait — with a one-clause proof from their real email]
2. [Trait — proof]
3. [Trait — proof]
4. [Trait — proof]
5. [Trait — proof]

## Mechanics (measured, not guessed)
- **Greeting:** [exact string(s) they open with — "Hi [first name]," / no greeting / "[Name] —"] · [when they skip it]
- **Sign-off:** [exact string(s) — "Best," / "— [Initial]" / none] · [does it vary by audience?]
- **Sentence length:** [median words/sentence; short-and-punchy vs. long-and-clausal]
- **Email length:** [median words/email; typical paragraph count]
- **Punctuation habits:** [em-dashes as connective tissue? exclamation points — never/rare/frequent? Oxford comma? ellipses?]
- **Formatting:** [bullets vs. prose; bold for asks; numbered next-steps?]
- **Capitalization / casing:** [sentence case, lowercase-casual, etc.]
- **Emoji / GIFs:** [never / sparingly / channel-dependent]

## Lexicon
- **Reaches for:** [words/phrases they actually use — "Let me be clear…", "net-net", "the read is…"]
- **Never uses:** [jargon or filler that would sound wrong — "leverage", "synergy", "circle back", "hope this finds you well"]

## Patterns in Motion
- **Opening move:** [context-first? question-first? the ask up top?]
- **Closing move:** [explicit next step? a clear ask? a deadline?]
- **Decision language:** [how they frame choices — "X because Y" vs. pros/cons list]
- **How they say no / push back:** [direct decline? soft redirect?]
- **How they show warmth:** [uses first names, remembers details, or all-business]

## Tone by Audience
[Only if the corpus shows a real shift. Example:
- With clients: more context-setting, more formal sign-off
- With team: assumes shared context, faster, more directive
- With investors/board: numbers-first, brief]

## What NOT to Do
[Things that would sound out of character — the fastest tells that a draft isn't theirs.]

## Examples (verbatim, from their real sent mail)
### Good (sounds like them):
"[Real sentence pulled from their recent sent emails that shows the voice]"
"[A second real sentence showing a different trait — e.g., how they make an ask]"

### Bad (doesn't sound like them):
"[Contrast example — a generic/corporate version of the same intent]"
```
