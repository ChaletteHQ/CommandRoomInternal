---
name: one-pager-composer
description: "Turn any topic, question, or pile of notes into a polished one-page executive brief in under 60 seconds. Produces a formatted .docx with headline, 3 key points, supporting data, and recommendation — saved to the project's deliverables/ folder, ready to send to a partner, customer, or board member. Use when the CEO says 'one-pager', 'one-pager on', 'write me a one-pager', 'make me a one-pager', 'I need a one-pager', 'need a one-pager', 'throw together a one-pager', 'single-page brief on', 'executive one-pager', 'turn these notes into a one-pager', 'one page summary', 'one page summary of', 'brief me on [topic] in one page'. Voice-calibrated via this skill's Voice Block per shared/VOICE_CALIBRATION.md. DOES NOT fire on requests for multi-page reports / recurring updates / decision memos (use memo-writer) or slide decks (use pptx / slide-deck). This is the shortest-form output skill — pick it when the answer fits on one page."
voice_block_last_refreshed: 2026-04-21
calibration_level: default
template_version: 2.7.1
---

## Skill Boundary (v2.1)

- **Use one-pager-composer for:** single-page executive briefs. Fixed 6-part skeleton (headline / subhead / 3 key points / supporting data / recommendation / footer). Output is always .docx. Fits on one US Letter page or it gets cut.
- **Use `memo-writer` for:** comparative tradeoffs OR longer recurring updates (decision docs, scope docs, strategy memos, board / investor narratives).
- **Use `pptx`/`slide-deck` for:** visual/presentation format. Slides, not pages.
- **Template pattern:** one-pager-composer establishes the shared "messy input → polished .docx" pattern. Parse → ground → structure → draft in voice → handoff to `docx` skill → save → return link.

## Voice Protocol (v3.0 — v2.7.1 architecture)

This skill follows the two-step draft-then-critique protocol defined in `shared/VOICE_CALIBRATION.md`. Voice lives in the `## Voice Block` section of this SKILL.md — NOT in a separate `VOICE_SAMPLES.md`. That file is deprecated as of v2.7.1.

Every one-pager draft:
1. Uses this skill's Voice Block (cadence, openers, vocabulary, punctuation, taboos — tuned for one-page executive brief register: sharp, numbers-first, headline-driven).
2. Applies audience modifier based on the stated audience (board/customer/team/partner) or defaults to internal executive.
3. Passes the Step 2 critique pass against the Voice Block + universal banned-phrase list.
4. Strips any banned LLM tells before return.

Corrections on user-edited drafts append to `_hq/voice/corrections-one-pager-composer.jsonl`. The corrections corpus drives the next Voice Block refresh when the operator runs the calibration protocol.

## Writer Contract (v3.7.1+ — substrate-aware)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Primary writer for:**
- `[project]/deliverables/[Topic]_OnePager_[YYYY-MM-DD].docx` (or `_hq/deliverables/` if no project scope) — the polished one-page brief.

**Appends to:**
- `_hq/data/events.jsonl` — event type `one_pager_drafted` with `{topic, audience, primary_thread_id, artifact_path, source_decision_ids[], source_intel_ids[]}`. This lets `insight-generator` detect topical patterns ("you've written 4 one-pagers about pricing this month") and `operator-report` surface one-pagers in the work-produced section.

**Reads from (v3.7.1+ substrate enrichment — was read-only over the data layer pre-v3.7.1):**
- The CEO's input text + relevant `[project]/SESSION_NOTES_*.md` + `PROJECT_CONTEXT.md` + `PROJECT_BRAIN.md` for project-specific facts.
- `_hq/data/entities.json` for org / person / project context (e.g., if the one-pager is about Acme Co, pull their record).
- `_hq/data/events.jsonl` for prior decisions on the topic (filter `type == "decision"` with topic match) — pulled into the brief's context so the one-pager doesn't contradict positions you've already taken. Source seqs go into `source_decision_ids[]` on the emitted event.
- `_hq/intel/*.md` for captured intel on the topic — surfaced as "what we know" if the one-pager is research-heavy. Source ids go into `source_intel_ids[]` on the emitted event.
- This skill's Voice Block + `shared/VOICE_CALIBRATION.md`.

**Conflict boundary:** sole writer of `one_pager_drafted` events. No collision with any other skill.

**Why the upgrade (v3.7.1 note):** pre-v3.7.1 this skill was read-only by design — a one-pager from 3 months ago was invisible to `insight-generator`, didn't show up in `operator-report` as work produced, and the topic-pattern detection ("you've made the same case 4 times") was impossible. The event write closes that loop while leaving the file-system deliverable shape unchanged.

---

# One-Pager Composer

**For:** CEOs who need to produce a sharp, single-page brief fast — for a partner, customer, board member, or team — without spending 45 minutes wrestling with Word.

## What It Does

Turn any topic or question into a polished one-page document in under 60 seconds. Input can be a prompt ("one-pager on our China supply chain exposure"), a link, a pasted article, or a loose pile of notes. Output is a formatted .docx saved into the current project's `deliverables/` folder, ready to email or print.

This is the **template skill** for the "messy input → polished output" family. It's the quickest wow in a demo — single prompt in, beautiful artifact out — and sets the structural pattern that future output-layer skills inherit.

## How to Use

```
"Write me a one-pager on [topic]"
"Give me a one-pager about [our Q3 margin drop / this acquisition target / the new OSHA rule]"
"One-pager: [headline]"
"Turn these notes into a one-pager" (with pasted text or uploaded file)
"Single-page brief on [topic] for [audience]"
```

If the user names an audience ("for the board", "for my sales team", "for a customer"), adjust tone and depth accordingly. If no audience, default to internal executive.

## Output Structure

Every one-pager uses this skeleton. No exceptions — consistency is the product.

1. **Headline** (1 line) — The single conclusion the reader should take away. Active voice, specific, no hedging.
2. **Subhead** (1 sentence) — Why this matters right now.
3. **Three Key Points** — Each one a tight paragraph, 2-4 sentences max. Lead with the claim, follow with the evidence. Bolded lead-in phrase.
4. **Supporting Data** — 3-5 bullets with numbers, dates, sources. If data is estimated or unavailable, say so plainly.
5. **Recommendation** — What should the reader do. One paragraph. Specific and time-bound.
6. **Footer** — Author, date, 1-line source note.

Total length: fits on one US Letter page with 1-inch margins, 11pt Calibri. If content is overflowing, cut — don't shrink type.

## How It Works

1. Parse the prompt for: topic, audience, any attached data, any attached context files.
2. If project context is available (user is in a client project folder), pull relevant session notes, PROJECT_CONTEXT.md, and recent deliverables for grounding.
3. If web research is needed and the user hasn't said "use my notes only," use the web research connector to pull 2-3 authoritative sources.
4. Draft using the skeleton above. Voice comes from the baked-in `## Voice Block` in this SKILL.md (Voice Protocol v3.0) — do not read external `VOICE_SAMPLES.md` files.
5. **Render via the canonical brief_writer (v3.13.8+ — Bug #53):** call `shared/scripts/brief_writer.py` `make_brief(brief_kind="one_pager", ...)`. Do NOT invoke the `docx` skill or hand-roll docx-js. brief_writer enforces canonical Calibri typography, navy heading hierarchy (Heading 1/2/3 per Bug #7), eyebrow label "ONE-PAGER", and runs the universal post-render leak scanner (Bug #57/#59/#54) automatically. Use the v3.13.8 `table` / `matrix` section primitives for tabular comparisons rather than synthesizing bullets.
6. Save to `[Current Project]/deliverables/` with filename `[Topic]_OnePager_[YYYY-MM-DD].docx`. If no current project context, save to `_hq/one-pagers/`.
7. Return the file link + a 2-sentence summary of what's in it. Done.

## File Naming

Format: `[Topic]_OnePager_[YYYY-MM-DD].docx`

Examples:
- `ChinaSupplyChain_OnePager_2026-04-20.docx`
- `Q3MarginDrop_OnePager_2026-04-20.docx`
- `AcquisitionTarget_Mercer_OnePager_2026-04-20.docx`

## Formatting Standards (handled by brief_writer canonical path)

The canonical `brief_writer.make_brief(brief_kind="one_pager", ...)` path already enforces:

- US Letter, 1-inch margins
- Calibri 11pt body, Calibri 16pt bold headline (navy #1F3A5F), 13pt section labels
- Headline centered or left-aligned per template
- Bullets use `LevelFormat.BULLET` — never manual unicode
- Tables only when the data begs for it; never use tables as dividers
- Footer in 9pt grey with author + date

## Voice Block

**Last refreshed:** 2026-04-21
**Calibration level:** default
**Sample count:** 0 (uncalibrated — generic sharp-executive defaults)

### Sentence cadence
- Typical length: 12-20 words
- Maximum (headline): 25 words — hard cap
- Maximum (key points / body): 30 words
- Short-punch openers: frequent ("The number is 17%.", "One question matters.")

### Openers (headline)
- Preferred: active-voice, specific, conclusion-first. "Margin fell 17% because of three supplier concentration bets." "We should exit the China hardware line by Q3."
- Avoided: "An analysis of…", "Considerations regarding…", "A review of our Q3 numbers"
- Never use: "This document aims to…", "It is important to note that…", "The purpose of this one-pager is to…"

### Vocabulary
- Uses: specific verbs, concrete numbers, named entities. "17% drop", "3 suppliers", "Q3 exit", "Sam's team"
- Avoids: "significant", "meaningful", "substantial", "leverage", "ecosystem", "holistic", "stakeholders" (name the group)
- Domain-specific: whatever the topic calls for — but name it, don't abstract it

### Punctuation
- Em-dashes: occasional; never two in one sentence
- Semicolons: rare
- Parentheticals: rare in one-pagers — the page is too tight
- Colons: acceptable before lists or definitions

### Structure
- Headline: single line, active voice, the conclusion. Never a question. Never a preamble.
- Subhead: one sentence on why-now. Dates or quantitative trigger preferred.
- Key points: bolded lead-in phrase + 2-4 sentence paragraph. Lead with claim, follow with evidence.
- Supporting data: 3-5 bullets, each with a number, date, or source. Bullets are short fragments, not full sentences.
- Recommendation: one paragraph, specific + time-bound. Names the action, the owner, the deadline.
- Footer: author + date + 1-line source note.

### Tone markers
- Register: sharp, executive, commit-to-a-view
- Self-reference: "I" or "we" — avoid passive voice
- Hedging: minimal — if the data is weak, label the placeholder; don't cushion the language
- Numbers over adjectives — always

### Taboos (per-skill)
- Never: "it is important to note that", "this document aims to", "in conclusion", "going forward", "synergies"
- Never pad to fill the page. A strong half-page beats a bloated full page.
- Never fabricate numbers to fill the supporting-data section. Mark `[PLACEHOLDER — confirm with the author]` and keep moving.
- Never write a question as the headline. The headline is the answer.

### Examples

**Example 1 — Headline + subhead (margin drop topic):**
```
# Q3 Margin Dropped 17%. Three Supplier Concentration Bets Are Why.

The problem compounds through Q4 unless we diversify the top-3 supplier
list by August 15.
```

**Example 2 — Key point paragraph:**
```
**Supplier concentration is the headline risk.** 62% of Q3 hardware
COGS came from three suppliers in Shenzhen. Two raised prices 9-14%
in September. The third signaled a 2027 capacity cut. A single-supplier
outage would stop the line for 4-6 weeks.
```

**Example 3 — Recommendation close:**
```
## Recommendation

Dual-source the top three SKUs by August 15. Sam's team owns the
supplier shortlist by July 1. Finance models the 180-day inventory
buffer by July 8. I'll chair a weekly Monday review until the second
source is qualified.
```

## Triggers

- "write me a one-pager"
- "give me a one-pager"
- "one-pager on"
- "one-pager about"
- "single-page brief"
- "brief me on [topic] in one page"
- "executive one-pager"
- "turn these notes into a one-pager"

## Gotchas

- **If the topic is ambiguous, ask ONE question before drafting.** "One-pager on the lawsuit" → ask: "which lawsuit?" Don't guess.
- **Don't pad to fill the page.** A strong half-page beats a bloated full page. If the topic genuinely doesn't need a full page, say so and deliver the shorter version.
- **If the user asks for a one-pager on a topic with no available data, return a draft with clearly-labeled placeholders.** Do NOT fabricate numbers to fill the data section.
- **Always invoke `docx` skill explicitly.** Never output the doc as markdown or inline text unless the user says "just draft it in chat."
- **Check the audience.** A one-pager for the board reads differently than one for the sales team. If audience isn't stated and isn't obvious from project context, ask.

## What It Doesn't Do

- Multi-page reports — use `memo-writer` (recurring updates / strategy memos) or a full report workflow
- Decision frameworks — use `memo-writer` for comparative tradeoffs (decision docs / scope docs)
- Slide decks — use `pptx` or the slide-deck skill

## Template Pattern for Other Composers

This skill establishes the shared pattern for all output-layer skills:

- Parse → Ground (project/web) → Structure → Draft in voice → Handoff to format skill → Save → Return link
- Output skeletons are fixed — the value is consistency, not creativity
- Always invoke the format skill (`docx`/`pptx`/`pdf`) rather than building files directly
- Always save into `deliverables/` with the naming convention
- Always return a file link + 2-sentence summary, nothing more

## Connected Tools

- **docx skill** — produces the Word file (MUST read SKILL.md first)
- **Session Notes / PROJECT_CONTEXT** — project grounding
- **Web research connector** — external data when needed
- **Voice Block** (baked into this SKILL.md) — user voice calibration (v3.0)
