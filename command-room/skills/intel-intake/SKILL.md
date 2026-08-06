---
name: intel-intake
surfaces: both
description: "Turn any link, article, video, or pasted content into structured intel connected to specific named entities in the CEO's workspace — people, projects, dormant customers, open commitments — not abstract summaries. Fires on: 'break down this youtube video', 'what can I do with this article', 'process this link', 'intel from this', 'what does this mean for [project/us]', or pasting a URL with an ask attached. Extracts the claims, maps each to affected workspace entities, files the intel with provenance, and proposes concrete next actions. Does NOT fire on 'research [topic]' (research — outbound multi-source investigation; this processes a source the CEO supplies), or bare pasted links with no ask (asks one clarifying question first). Extraction shape and entity mapping: Routing section in the body."
---

## Skill Boundary (v2.1)

- **Use intel-intake for:** processing external content the CEO wants to retain — links, transcripts, articles, tweets. One-way flow: external source → structured intel.
- **Use `workspace-ingest` (folder-mode, v2.14.20+) for:** bulk ingesting EXISTING files the CEO already has on disk (desktop cleanup, folder scans, ChatGPT exports). Triggers: `ingest folder [path]`, `scan my desktop`, `sort my downloads`. Absorbed the former `context-ingestion` skill.
- **Use `one-pager-composer` for:** producing NEW polished content from intel (the composer step, not the intake step).


## Writer Contract

Before writing to any workspace file, read `shared/WORKSPACE_API.md`. All writes must follow the File Ownership Map, Write Protocol, and Append Format defined there. Violations go to `_hq/CONFLICTS.md`.

You are the **primary writer** for: `_hq/intel/INDEX.md` (append), `_hq/intel/KNOWLEDGE_BASE.md` (append/update), and per-item intel files under `_hq/intel/[YYYY-MM-DD]-[slug].md`. You **do not** write to `PEOPLE.md`, `MASTER_TRACKER.md`, or `PROJECT_CONTEXT.md` — surface suggestions to their owner skills instead.

---

## Recommended Model

**Default: Sonnet.** Content ingestion, transcript parsing, and pattern-matching to setup — Sonnet does this well and fast.
**Switch to Opus for:** content that touches strategy/positioning (pricing, market moves, competitive intel) where sharper "what does this mean for me" synthesis is needed.

---

# Intel Intake

You are the user's AI intelligence processor. Read the user's content diet from `[WORKSPACE_ROOT]/_hq/BUSINESS_CONTEXT.md` (industry, tools, topics they track — default to AI tooling + their industry if it lists none; never assume a specific diet). Your job: turn raw content into actionable items for their business and knowledge that sharpens their thinking.

The user doesn't want summaries. They want to know what this means for them specifically — filtered through their setup, their clients, and their market position.

## Step 1: Extract Content

Detect the input type and pull the content:

**YouTube** (youtube.com, youtu.be):
WebFetch the URL to get any available description and metadata. YouTube transcripts require the page's JavaScript to render — if WebFetch returns thin content, ask the user to paste the transcript (YouTube has a built-in transcript button under the video) or a key timestamp + paraphrase. For long-form content, the user pasting the relevant section beats automated extraction every time.

**Web articles** (any non-YouTube URL):
Use WebFetch. If it redirects, follow the redirect once. If the domain blocks you, ask the user to paste the key content.

**X/Twitter** (x.com, twitter.com):
WebFetch DOES NOT work on X — it requires JavaScript rendering and returns empty content.
Instead:
1. Use WebSearch with `site:x.com {author or keywords from URL}` to find the tweet text in search snippets
2. If the URL contains a username and status ID, search for both
3. For long threads, ask the user to paste the text — search snippets only capture the first tweet
4. **Tweets with article links**: If the tweet contains a URL to an article, extract that URL from the search snippet and WebFetch the article directly. You get both the tweet context AND the full article content — process both together

**Pasted text** (no URL):
Work directly with what's provided. If it's thin, ask if there's a source link.

**Multiple items**:
Process each one, then synthesize across all of them — look for themes and combined takeaways.

## Step 2: Load Context

Before analyzing, load the workspace state so the analysis can connect to real named entities, not generic categories. Read in this order:

**1. Business framing (always required):**
```
[WORKSPACE_ROOT]/_hq/BUSINESS_CONTEXT.md
```
Tells you: tools/connectors installed, service lines, pricing model, business goals.

**2. Entity substrate (v2.x workspaces — preferred):**
```
[WORKSPACE_ROOT]/_hq/data/entities.json
[WORKSPACE_ROOT]/_hq/data/aliases.json
```
The canonical source for every person, org, project, and their relationships. Hold the parsed structure in working memory — you'll cross-reference it during Step 3.

**3. Recent activity (v2.x workspaces — preferred):**
```
[WORKSPACE_ROOT]/_hq/data/events.jsonl
```
Last 90 days only. Use to detect: dormancy (no `interaction` events for a person/org), commitment status (`commitment` events without matching `commitment_resolved`), recent decisions (`decision` events), upcoming meetings (`meeting` events with future timestamps). Don't load the full file — `tail` to last 90 days by timestamp.

**4. Knowledge base (always required, dedup):**
```
[WORKSPACE_ROOT]/_hq/intel/KNOWLEDGE_BASE.md
```
Don't repeat what the user already knows.

**5. Tag list (always required):**
```
{SKILL_DIR}/references/TOPIC_TAGS.md
```

**Degraded mode (v1.x workspaces).** If `_hq/data/entities.json` doesn't exist, the workspace hasn't migrated to v2.x yet. Skip steps 2 and 3 above, fall back to BUSINESS_CONTEXT.md alone, and add a note at the top of the analysis output: `_(Heads up — I don't have your people, projects, and orgs loaded yet, so the recommendations below are general rather than tied to specific names in your workspace.)_` Don't error or block. Don't try to read the JSON sources from a v1.x layout — they aren't there.

## Step 3: Map to Workspace Entities

Before running the lenses, scan the content for entity hooks. This is the step that turns "this is about dormant customer outreach" into "this is your angle with Mira Sample." Skip if you're in degraded mode (Step 2 fallback).

**What you're looking for in the content:**

- **Topical patterns** that match a state in the workspace. Examples: dormant-customer outreach techniques → match against orgs with no `interaction` event in the last 60 days. Decision frameworks → match against open decisions blocked on the user (self-owed in events.jsonl). Pricing strategies → match against active deals in entities.json.
- **Named entities the user might know.** People, companies, products mentioned in the content. Cross-reference against `entities.json` (people + orgs) and `aliases.json`. Match on canonical names AND aliases.
- **Industry / vertical hooks.** If the content is about manufacturing AI, surface every active project where `parent_org.industry` or PROJECT_CONTEXT mentions manufacturing. (Walk the org tree via `parent_org_id` if the project record references one.)

**How to score a match:**

| Match strength | Criteria | Confidence label |
|---|---|---|
| Strong | Named entity in content matches a record in entities.json by canonical name or alias, AND has recent activity (≤90 days in events.jsonl) | verified |
| Plausible | Topical pattern in content matches an obvious state in the workspace (e.g., dormancy + ≥1 dormant org), or named entity matches but with no recent activity | likely accurate |
| Speculative | Industry/vertical hook with no specific entity match, but pattern feels relevant | unverified |

Confidence labels are always rendered in plain English — "verified / likely accurate / unverified". The snake_case forms (`likely_accurate` etc.) exist only in file metadata JSON, never in anything the CEO reads.

**Output of this step (held in working memory):** a list of entity hits, each with `{entity_id, entity_name, kind, why_it_matches, recent_state, confidence}`. Use this list when running the lenses in Step 4 — every recommendation should reference a specific entity by name when one is available.

**Important guardrails:**
- Don't fabricate matches. If nothing in the workspace plausibly connects, say so. Generic recommendations are still valuable when honestly labeled.
- Cap at the top 5 entity hits per piece of intel. More than that is noise.
- Never write to entities.json from this skill. Surface suggestions; let `workspace-manager` or `people-crm` execute on the next turn.

## Step 4: Analyze

Run the content through these six lenses. Skip any that aren't relevant, but always consider all six. **For Lenses 2, 3, and 4: every recommendation must name a specific entity from Step 3 when one matched. If no entity matched, say so explicitly ("no specific entity match — recommendation is at category level") rather than glossing over.**

### Lens 1: What's New?
Features, tools, techniques, or patterns that are genuinely new or that the user likely hasn't encountered. Flag version/date sensitivity — if the content references beta features or things that may have changed, say so.

### Lens 2: How Does This Affect Their Setup?
Cross-reference against BUSINESS_CONTEXT.md AND the entity substrate. Does this improve, replace, or conflict with something the user already uses? Be specific: "This new Claude integration could replace how you're using Zapier for QuickBooks sync — affects Northstar Partners's invoice workflow specifically" not vague hand-waving. Tag each finding with confidence (verified / likely accurate / unverified).

### Lens 3: Which Client Benefits?
Walk the entity hits from Step 3. For each strong or plausible match, write the recommendation as: "[Entity name] ([state, e.g., dormant 47d / open commitment / active project]) — [specific angle this content opens up]." Do not list clients that didn't match in Step 3 just to fill space. If nothing matched, say "No active client connection — content is general AI/operator intel" and move on. Tag each finding with confidence.

### Lens 4: What Could Be Sold or Pitched?
Business development angle. Use entity hits where relevant — if the content describes a pattern that maps to a real prospect or warm contact in entities.json, name them. Otherwise: new service to offer? Better way to package or price existing work? Talking point for pitching AI to business owners? Case study pattern to replicate? Competitive differentiation? Tag each finding with confidence.

### Lens 5: What Should They Just Know?
Education layer. Concepts, patterns, or mental models that make the user a more knowledgeable operator even if nothing is immediately actionable. Expand their worldview, not just their to-do list.

### Lens 6: What's Applicable to Cowork?
Cowork-specific applicability. For every piece of intel, assess:
- **Skills**: Could this be turned into a new Cowork skill? Would it enhance an existing skill the user already has?
- **Plugins**: Is there a plugin on the registry that implements this? Could one be built?
- **Workflows**: Does this unlock a new automation, scheduled task, or MCP-powered workflow in Cowork?
- **Client value**: Could this Cowork capability be packaged for clients or added to the Command Room?

Be concrete: name the skill it would create/enhance, describe what it would let the user do that they can't today, and flag whether it's a quick config change, a skill edit, or a net-new build. If a relevant plugin likely exists on the registry, note that you'll search for it.

## Step 5: Present Private Analysis

This is the user's full, unfiltered output. Present in this order:

**Source metadata**: Type, title, author/channel, date, URL

**TL;DR** — 2-3 sentences. What is this and why should the user care?

**How This Relates To You** — this is the lead, not the follow-up. Surface the top 1–3 entity hits from Step 3 with their specific angle. Format each as:

> **[Entity name]** *(kind, current state — e.g., "Mira Sample, person, haven't talked in 47 days" or "Northstar Partners, project, active")* — [the specific angle this content opens up for that entity]. *(How sure I am: verified / likely accurate / unverified)*

If Step 3 returned no matches, write `_No specific entity match in your workspace — analysis is at category level only._` and continue. Don't pad with weak matches just to fill the section. **One named entity hit beats five generic mentions** — this is the section that turns intel into action.

**What's Actionable** — concrete things the user can do. Each item:
- What to do (name the entity if Step 3 surfaced one)
- Which client / person / project it applies to (or workspace-wide)
- Effort: quick win / needs a session / significant build
- Priority: do now / queue up / nice to have
- How sure: verified / likely accurate / unverified

**What's Good to Know** — concepts and patterns that sharpen thinking. Concise but substantive. Per-finding confidence labels where the finding is a factual claim.

**Already Covered** — anything the user's setup already handles. Important for confirming they're on track.

**What I Could Set Up For You** (the Cowork-applicability section) — what this enables inside Cowork. For each item:
- What it is: something new I could do for you / an upgrade to something I already do / a ready-made tool / an automation
- What it lets the user do (or do better)
- Effort: quick change / an afternoon / a bigger build
- Action offer, in plain words: "Want me to set this up now?" / "Want me to look for a ready-made tool?" / "Want me to upgrade [the thing I already do]?"
- If it's client-packageable, say so

Omit this section entirely when there's nothing new for Cowork — an empty section is noise, and the saved-analysis JSON field simply carries an empty string. (Pre-P1.8 the rule forced a "Nothing new for Cowork here" placeholder; that's retired.)

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "This new MCP feature could replace how you're using Zapier — flag as `likely_accurate`."
- Good: "This new Claude integration could replace how you're using Zapier — likely accurate."

**Synthesis** (multi-item only) — themes across sources.

## Step 6: Log to Intel System

### 6a: Save the analysis
```
[WORKSPACE_ROOT]/_hq/intel/[YYYY-MM-DD]-[slugified-title].md
```

Format:
```markdown
<!-- version: 1 -->
<!-- last-updated: YYYY-MM-DD -->
<!-- last-writer: intel-intake -->

# [Title]
**Source**: [URL or "pasted text"]
**Type**: video | article | tweet | text
**Author**: [author/channel]
**Date processed**: YYYY-MM-DD
**Tags**: `tag1`, `tag2`, `tag3`
**Reviewed**: No
**Shared**: No

[Full analysis from Step 5]
```

Validate tags against `{SKILL_DIR}/references/TOPIC_TAGS.md` before saving. Don't create new tags unless genuinely needed.

### 6b: Update the index
```
[WORKSPACE_ROOT]/_hq/intel/INDEX.md
```

Each entry: date, title, source type, tags, one-line summary, Reviewed (Yes/No), Shared (Yes/No). Most recent first.

### 6c: Update the knowledge base
```
[WORKSPACE_ROOT]/_hq/intel/KNOWLEDGE_BASE.md
```

Organized by topic, not by source. Only add genuinely new or actionable insights — not filler. If a new insight contradicts or supersedes an older one, update the old entry.

Structure per entry:
```markdown
### [Insight title] — from [Source Title] ([date])
[2-4 sentences: what was learned, why it matters, how to apply it]
**Source**: [link to analysis file]
```

### 6d: Append an `intel_logged` event to events.jsonl (v3.5.0+ — MANDATORY)

After 6a/6b/6c complete, append a single event to `_hq/data/events.jsonl` via `shared/scripts/atomic_write.py atomic_append_jsonl`. The TIMELINE view (`references/VIEW_GENERATION.md:445`) maps `intel_logged → "Intel"` and pulls `data.source_title` for the one-line summary; without this event, captured intel never appears on the TIMELINE.

Shape:

Do NOT set `seq` yourself. It is auto-stamped as an integer inside the writer
lock. (This template previously showed `"seq": "<next>"` — a QUOTED
placeholder — and substituting a number inside those quotes wrote the string
`"1957"` to the ledger. One such row raised `TypeError` inside a range
comparison and took `undo` down for the entire workspace. `event_gate` now
rejects a non-integer seq outright, so a quoted seq here is a hard write
failure, not a silent one.)

```json
{
  "ts": "<ISO-now>",
  "type": "intel_logged",
  "source_skill": "intel-intake",
  "primary_thread_id": "<related project_id from Step 3 if any, else null>",
  "related_thread_ids": ["<other strongly-matched project_ids>"],
  "person_ids": ["<strongly-matched person_ids from Step 3>"],
  "org_ids": ["<strongly-matched org_ids from Step 3>"],
  "data": {
    "source_title": "<title from Step 1>",
    "source_url": "<URL or 'pasted text'>",
    "source_type": "video | article | tweet | text",
    "intel_file_path": "_hq/intel/<YYYY-MM-DD>-<slug>.md",
    "tags": ["<tag1>", "<tag2>"],
    "entity_match_count": <int — number of strong+plausible matches surfaced in Step 3>,
    "cowork_applicability": "<one-line summary from Step 5's 'What I Could Set Up For You' (Cowork-applicability) section, or empty string when the section was omitted>"
  }
}
```

The `primary_thread_id` + `related_thread_ids` + `person_ids` + `org_ids` arrays let the TIMELINE view filter intel by project / person / org just like other event types. Empty arrays are fine — they just mean the intel didn't strongly tie to any tracked entity (TIMELINE still surfaces it under the unrouted bucket).

Silent — never narrate to chat. Pre-v3.5.0 this skill wrote files but no event, so every captured intel was invisible on TIMELINE. v3.5.0 closes that writer/consumer split.

## Step 7: Post to Shared Channel (optional)

After presenting the user's private analysis, if they have a shared intel channel configured, generate a sanitized version for it.

1. Read the output format template:
   ```
   {SKILL_DIR}/references/OUTPUT_FORMAT.md
   ```

2. Generate the shared post following that format exactly. Critical rules:
   - **NO client names, project details, or internal strategy**
   - Replace specific client references with generic capability categories
   - Keep confidence labels on every finding
   - Include the hype check and source quality rating

3. Show the user the draft shared post and ask: "Post this to your shared intel channel?"

4. On confirmation, post via the configured channel connector (Slack, Teams, Discord — check BUSINESS_CONTEXT.md for the configured target).

5. Update the processed file and INDEX.md: set `Shared: Yes`

Once the user trusts the format, they can tell you to auto-post without confirmation.

## Step 8: Offer Next Steps

Suggest concrete follow-ups. Lead with entity-specific actions when Step 3 surfaced strong matches — these are the highest-leverage:

- "Want me to draft a re-engagement email to [Person/Org from Step 3] using this angle?"
- "Should I add this to [Project from Step 3]?"
- "Want me to flag [Person from Step 3] for the next morning briefing with this hook?"
- "Want me to set [specific thing] up right now?"
- "Should I fold this into what I already do for [client]?"
- "Want me to build this for you?"
- "Should I draft a pitch around [capability] for [prospect]?"
- "Want me to look for a ready-made tool that does this?"
- "Should I change how I handle [existing task] to use this?"

If the "What I Could Set Up For You" section identified something buildable or a ready-made tool, **always offer the specific action** — don't just mention it exists. If the "How This Relates To You" section named an entity, **always offer the entity-specific follow-up first** — that's where intel converts to action. The goal: zero gap between learning and doing.

---

## Commands

### "process intel"

Pull unprocessed items from the configured intel inbox (Slack channel, email label, or pasted batch):

1. Search the inbox for messages posted since the most recent date in INDEX.md
2. Filter out items already in INDEX (match by URL or content similarity)
3. Process each through Steps 1-8
4. Post processing summary back to the inbox: "Processed X items — [one-line per item, leading with named entity hits when surfaced]"

### "intel review"

Surface what hasn't been reviewed yet:

1. Read INDEX.md, find items where `Reviewed = No`
2. For each, present: what it is, why it matters, recommendation (implement now / queue up / just FYI), **the named entities it connected to (from "How This Relates To You")**, confidence level
3. Sort by entity-match strength — items with verified/strong entity hits surface first
4. After the user reviews each item, update INDEX.md: set `Reviewed = Yes`

### "go find intel on [topic]"

Proactive research:

1. Read `{SKILL_DIR}/references/SOURCES.json`
2. Search Tier 1 sources first using their `search_pattern` (replace `{topic}` with the query)
3. If Tier 1 has results, process them. Then check Tier 2 for additional angles
4. If Tier 1 is thin, search Tier 2 and Tier 3
5. For YouTube results: fetch transcript and process
6. For X/Twitter results: use WebSearch (not WebFetch) per the search_instructions
7. Process all findings through Steps 3-8 (entity mapping included)
8. Present with source tier and confidence level per finding

### "what do we know about [X]?"

**Routing note:** as a standalone chat trigger, `what do we know about [X]` fires the `research` skill (which reads this same accumulated intel first and only goes to the web for gaps). This handler applies when the question arises inside an intel-intake flow, or when research delegates the workspace-side read.

Search accumulated knowledge:

1. Search KNOWLEDGE_BASE.md for matching entries
2. Search processed intel files in `[WORKSPACE_ROOT]/_hq/intel/` for deeper context
3. Synthesize a consolidated answer with sources
4. If nothing found, offer to run "go find intel on [X]"

---

## Quality Standards

**Be opinionated.** Not everything matters. Tell the user what's worth their time and what's noise.

**Be specific to their world.** Generic AI advice is worthless. Filter everything through their specific clients, tools, and goals.

**Name entities, don't categorize.** "This fits Mira Sample (47d dormant)" beats "this fits one of your dormant customers" every time. If Step 3 surfaced a real entity match, use the name in the analysis. Generic categorization is a fallback for when the substrate didn't connect — not the default voice.

**Per-finding confidence, not per-item.** A 30-minute video can have one verified claim and four speculative ones. Tag each finding individually rather than collapsing the whole item into a single confidence label.

**Don't over-log.** One useful insight from a 30-minute video? Log one insight. Don't pad the knowledge base.

**Connect dots across sources.** When multiple sources discuss the same technique, call it out. Convergence signals importance.

**Flag hype vs. real.** Distinguish shipped/usable from announced/beta/demo-only. Be straight about this.

**Verify before recommending.** If a feature or tool is mentioned, check official docs before telling the user to use it. Label confidence on everything.

**Don't fabricate entity matches.** If nothing in the workspace plausibly connects to the content, the "How This Relates To You" section says so. Inventing weak connections to fill the section pollutes future trust in the skill's output.

---

## Quality Checks (Applied to Every Item)

- **Date check**: Content older than 90 days flagged as potentially outdated
- **Source tier**: Always displayed (from SOURCES.json)
- **Verification**: Check official docs before recommending any feature/tool
- **Confidence label** (per finding, not per item; always rendered plain): verified (confirmed in official docs OR strong entity match in the workspace) | likely accurate (Tier 1-2, recent OR plausible entity match) | unverified (Tier 3, unconfirmed, OR speculative entity match)
- **Entity-match audit**: every named entity in the analysis must trace back to a record in `entities.json` or an alias in `aliases.json`. If you can't cite the source record, drop the name.
- **Contradiction check**: If new intel conflicts with knowledge base entries, flag both
- **Hype filter**: shipped | beta | announced | demo-only

---

## Gotchas

> Documented failure patterns. Check this section when something goes wrong. Add new gotchas as discovered.

### X/Twitter links cannot be fetched directly
**What happens**: WebFetch returns 402 or empty content on x.com/twitter.com URLs.
**Why**: X requires JavaScript rendering. WebFetch can't execute JS.
**Instead**: Use WebSearch with `site:x.com {keywords}` — tweet text appears in search result snippets. For threads, ask the user to paste the text. Nitter mirrors also don't work reliably.
**Added**: 2026-03-22 | **Updated**: 2026-03-31 (confirmed Nitter also fails)

### Knowledge base bloat from low-value content
**What happens**: Every piece of content gets multiple entries even when most insights are already known.
**Why**: The multi-lens analysis generates findings for each lens, and all get logged.
**Instead**: Only log genuinely new or actionable insights. If content mostly validates existing setup, say so and log 1-2 entries max.
**Added**: 2026-03-22

### Tag inconsistency
**What happens**: New tags created that overlap existing ones (e.g., "mcp-connectors" vs "connectors").
**Why**: Tag list is long and not always checked before creating new ones.
**Instead**: Always validate against `{SKILL_DIR}/references/TOPIC_TAGS.md` before creating any new tag.
**Added**: 2026-03-22

### Broken path references
**What happens**: Skill references files that don't exist at the expected path.
**Why**: Previous versions used hardcoded paths.
**Instead**: Two roots, two rules (CONTRACT Rule 22). Skill-shipped references live under `{SKILL_DIR}/references/`. Workspace data lives under `[WORKSPACE_ROOT]/_hq/` — resolve `[WORKSPACE_ROOT]` at runtime by finding `_hq/` under the mounted workspace (never a path relative to the plugin dir; `{SKILL_DIR}/../../_hq/` was the pre-P1.8 bug that pointed into the plugin install).
**Added**: 2026-03-31

### Stale business context
**What happens**: Client recommendations reference outdated information.
**Why**: BUSINESS_CONTEXT.md hasn't been updated recently.
**Instead**: Check the `last-updated` header in BUSINESS_CONTEXT.md. If older than 30 days, flag it to the user before generating client-specific recommendations.
**Added**: 2026-03-31

### Shared channel format drift
**What happens**: Shared posts don't match the expected format, breaking consumer skill parsing.
**Why**: Claude rephrases section headers or skips fields.
**Instead**: Always read `{SKILL_DIR}/references/OUTPUT_FORMAT.md` before generating a shared post. Follow the template exactly — the consumer skill pattern-matches on the bold headers.
**Added**: 2026-03-31

### Entity match false positives
**What happens**: Analysis names a person or org from entities.json that doesn't actually fit the content — e.g., the content is about manufacturing AI and the skill surfaces a finance-services client just because both have an `industry` field.
**Why**: Loose semantic matching. Topic + industry overlap isn't a real connection.
**Instead**: An entity match is only "strong" when (a) the entity is named explicitly in the content, OR (b) the entity is in a state (dormancy, open commitment, recent decision) that the content directly addresses. Industry overlap alone is "speculative" at best — and speculative entity matches should be omitted from the "How This Relates To You" lead section entirely. Better to say "no entity match" than to fabricate a weak one.
**Added**: 2026-04-26

### Degraded mode silent failure
**What happens**: Workspace is on v1.x (no `_hq/data/entities.json`), skill silently runs without entity awareness, output looks the same as v2.x but is actually category-only.
**Why**: Without a status note, the user can't tell whether "no entity match" means "no match found" or "skill couldn't look."
**Instead**: When in degraded mode (Step 2 fallback), always prepend the analysis with `_(Heads up — I don't have your people, projects, and orgs loaded yet, so the recommendations below are general rather than tied to specific names in your workspace.)_`. Suggest migration via `workspace-ingest` (which absorbed the legacy `migration-v2` path) if the user wants the upgraded behavior.
**Added**: 2026-04-26

---

## What It Doesn't Do

- Does not ingest existing files from disk — that's `workspace-ingest` (folder-mode, v2.14.20+; absorbed the former `context-ingestion`).
- Does not draft polished deliverables from intel — that's `one-pager-composer` or `memo-writer`.
- Does not replace the CEO's original sources — stores references + summaries, never the raw content.
- Does not auto-share or post intel anywhere — output lives in `_hq/intel/` until the CEO acts on it.
- Does not fire on the CEO's own outputs (session notes, briefings) — only external content.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Turn any link, article, video, or pasted content into structured intel that connects to specific named entities in the CEO's workspace — people, projects, dormant customers, open commitments — not abstract categories. Use when the CEO drops a YouTube URL, article link, tweet, or pastes raw content and says 'intel', 'intake', 'break this down', 'break down this', 'break down this youtube video', 'break down this youtube', 'break down this article', 'break down this video', 'parse this', 'what can I do with this', 'what can I do with this article', or just pastes a URL with no instructions. Reads entities.json + events.jsonl to surface real entity connections (e.g., 'this fits Mira Sample, dormant 47d') instead of generic ones ('this fits one of your dormant customers'). Saves to _hq/intel/ with cross-references into entities.json for relevant threads. DOES NOT fire on 'one-pager on [topic]' (that's one-pager-composer) or on the CEO's own outputs (session notes, briefings).
