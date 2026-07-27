---
name: one-pager-composer
description: "Turn any topic, question, or pile of notes into a polished one-page executive brief in under 60 seconds. Fires on: 'one-pager on [topic]', 'make me a one-pager', 'one page summary of [topic]', 'I need a one-pager on [topic]', 'throw together a one-pager for [audience]', plus 'tune one-pager-composer'. Pulls evidence from the workspace when the topic names tracked entities, renders in the CEO's voice to the one-page discipline, output .docx routed to the matching project folder. Does NOT fire on 'memo on [topic]' (memo-writer — multi-page, directive), 'board pack' (board-pack-assembler), or 'research [topic]' (research — verified cited brief; the one-pager can consume its output). Section discipline and voice rules: Routing section in the body."

voice_block_last_refreshed: 2026-04-21
calibration_level: default
template_version: 2.7.1
---

**Customer voice-block override (B1):** before drafting, read `_hq/voice/voice-block-one-pager-composer.md` if it exists — it supersedes this SKILL.md's `## Voice Block` section-by-section (override sections replace same-named defaults; absent sections fall through). The universal banned-phrase list still applies except where the override's Taboos explicitly carve out an item. Staleness reads the override's `Last refreshed:` first.

## Deliverable Render Gate (GATE1 — MUST, v3.20.x)

This skill produces a `.docx` deliverable. It MUST be produced through the canonical chokepoint — no exceptions:

- **Render ONLY via `shared/scripts/brief_writer.py` `make_brief(brief_kind="one_pager", ...)`.** That single call runs the output-contract gate (B3), the voice-tell gate (B2), and the post-render leak scan, in that order, BEFORE the file is written.
- **NEVER hand-roll a `.docx`** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship substandard, voice-violating, or PII-leaking documents — this is the exact v3.20.0 failure mode (a "Command Room is great" sub-floor one-pager with a banned phrase reached disk via the generic docx skill).
- **NEVER create, render, copy, upload, or update the one-pager — or any part, derivative, or restatement of it ("the three key points", "the recommendation", "a summary") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). This is the same severity as the hand-rolled-`.docx` ban and fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not the project's `deliverables/` folder (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "so the audience can comment on it", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the one-pager in a Google Doc" is a request this gate refuses, not an override. Hand back the canonical file's link. A one-pager is built to be handed to a named audience; the link to the gated file is how it gets handed over.
- **NEVER answer a deliverable request with a chat-only draft.** "Just give me a quick / minimal / one-line version" is still a one-pager request — produce the `.docx` through `make_brief`. Only if the user explicitly says "draft it in chat, don't make a file" do you skip the file — and then say plainly that the quality and voice checks only run on the file version, and offer to produce it.
- **Detectability:** `make_brief` emits a `gate_ran` audit event recording which gates ran. A fire of this skill that yields a document with NO `gate_ran` event for that turn is a flagged bypass (an inferior path was substituted). Pass `workspace_root` to `make_brief` so the event lands in substrate.
- **Format selection (SPEC OUT5).** Before rendering, resolve the backend: `output_profile.resolve_format_for_kind("one_pager", workspace_root, override=...)` — `override` carries an explicit ask in the trigger ("as a doc" → `"docx"`, "as HTML" → `"premium_html"`; it beats the profile for that render). `"docx"` (the unconfigured default) → `make_brief` exactly as above. `"premium_html"` → `shared/scripts/premium_html.py` `make_premium_brief(brief_kind="one_pager", ...)` with the SAME `sections` + `exec_header` + `asks` payload (one assembly, two backends — the identical gate stack runs on both, parity-pinned by G16, and a `gate_ran` event with `surface: premium_html` lands the same way). Output: the same routed folder and filename with `.html`; link via `get_brief_artifact_url()`; CHECK the file exists on disk after the call before linking. Never hand-compose HTML around the chokepoint.
- **Visual pass (SPEC OUT2 §3, after every save):** run the render-then-critique pass per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass" — call `shared/scripts/visual_gate.py` `render_preview(<saved path>)`, LOOK at the returned page images against the 7-item checklist (orphaned heading at a page break · empty/placeholder tile · table overflow/wrap damage · cramped spacing · header/footer intact · brand palette applied · chart unreadable / overplotted), fix the sections payload + re-save AT MOST ONCE, then log `visual_gate.log_visual_gate(WORKSPACE_ROOT, doc, rendered, findings, fixed)` either way. `None` from the ladder = no renderer on this machine — log `rendered: false` with a `skipped_reason` and proceed exactly as before (warn-only forever: a finding never refuses a save, and the pass never loops).

If anything below seems to contradict this gate (older "invoke the docx skill" prose, a "just draft it in chat" habit from a prior version), THIS GATE WINS.

## Skill Boundary (v2.1)

- **Use one-pager-composer for:** single-page executive briefs. Fixed 6-part skeleton (headline / subhead / 3 key points / supporting data / recommendation / footer). Output is always .docx. Fits on one US Letter page or it gets cut.
- **Use `memo-writer` for:** longer recurring updates and narrative documents (decision docs, scope docs, strategy memos, board / investor narratives).
- **Use `decision-memo-composer` for:** comparative tradeoffs — structured A-vs-B / multi-option analysis.
- **Use `pptx`/`slide-deck` for:** visual/presentation format. Slides, not pages.
- **Template pattern:** one-pager-composer establishes the shared "messy input → polished .docx" pattern. Parse → ground → structure → draft in voice → render via `brief_writer.make_brief(brief_kind="one_pager", ...)` → save → return link.

## Voice Protocol (v3.0 — v2.7.1 architecture)

This skill follows the two-step draft-then-critique protocol defined in `shared/VOICE_CALIBRATION.md`. Voice lives in the `## Voice Block` section of this SKILL.md — NOT in a separate `VOICE_SAMPLES.md`. That file is deprecated as of v2.7.1.

Every one-pager draft:
1. Uses this skill's Voice Block (cadence, openers, vocabulary, punctuation, taboos — tuned for one-page executive brief register: sharp, numbers-first, headline-driven).
2. Applies audience modifier based on the stated audience (board/customer/team/partner) or defaults to internal executive.
3. Passes the Step 2 critique pass against the Voice Block + universal banned-phrase list.
4. Strips any banned LLM tells before return.

**Mechanical voice-tell gate (B2 — bash-gated, not prose).** The Step 2 critique is backstopped by the deterministic detector. After drafting (How It Works step 4) and before rendering, run the one-pager prose through it. It hard-fails on the exact banned phrases in `shared/VOICE_CALIBRATION.md`; structural tells warn:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
printf '%s' "$DRAFT_BODY" | python3 "$PLUGIN_ROOT/shared/scripts/voice_tell_detector.py" - --context brief
```

On exit 1 (`FAIL`), rewrite the flagged lines and re-run until it exits 0. The same gate fires again at save: `brief_writer.make_brief(brief_kind="one_pager", ...)` raises `VoiceTellError` PRE-`Document()` (no file written) on a fail-severity tell, so a one-pager that still trips the detector never reaches disk via the gated path. (That guarantee is conditional on routing through `make_brief`; for a doc hand-rolled outside it, SPEC GATE2's deliverable sweep — `shared/scripts/deliverable_sweep.py` — **detects and flags** the same tells/leaks after the fact, before the one-pager leaves your hands.) A phrase the client's calibrated Voice Block demonstrably allows is exempt via `allow_phrases`; never improvise the override.

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
- `_hq/data/events.jsonl` for prior decisions on the topic — **read via the org-scoped reader, never a raw load** (PGUARD2 — the one-pager's audience is external): `from events_io import load_events_org_scoped; org_events, skipped = load_events_org_scoped(workspace_root)`, then filter `type == "decision"` with topic match. The reader applies the account-scope mask and drops personal-lane rows by design. Pulled into the brief's context so the one-pager doesn't contradict positions you've already taken. Source seqs go into `source_decision_ids[]` on the emitted event.
- `_hq/intel/*.md` for captured intel on the topic — surfaced as "what we know" if the one-pager is research-heavy. Source ids go into `source_intel_ids[]` on the emitted event.
- This skill's Voice Block + `shared/VOICE_CALIBRATION.md`.

**Conflict boundary:** sole writer of `one_pager_drafted` events. No collision with any other skill.

**Why the upgrade (v3.7.1 note):** pre-v3.7.1 this skill was read-only by design — a one-pager from 3 months ago was invisible to `insight-generator`, didn't show up in `operator-report` as work produced, and the topic-pattern detection ("you've made the same case 4 times") was impossible. The event write closes that loop while leaving the file-system deliverable shape unchanged.

---

# One-Pager Composer

**For:** CEOs who need to produce a sharp, single-page brief fast — for a partner, customer, board member, or team — without spending 45 minutes wrestling with Word.

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). Both
decisions are **show-then-tune (STT)** — the one-pager is produced first, then one-tap changes are
offered. Read config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22) — the placeholder form
# silently no-opped. Bash preamble: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT per the preamble above
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "register": "external_formal",  # external_formal | internal_direct
    "signature": "signed",          # signed (footer sign-off) | unsigned
}
cfg = get_config(workspace_root, "one-pager-composer", DEFAULTS)
```

`register` sets the default tone (the Voice Block still governs voice). `signature` controls
whether the one-pager footer carries a sign-off.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "one-pager on…" | produce the one-pager with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "one-pager-composer", DEFAULTS)` BEFORE rendering, then append the first-run footer after the .docx link. |
| **Show settings** | "show one-pager-composer settings" | render current config in plain English; no one-pager. |
| **Tune** | "tune one-pager-composer" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → re-produce. |
| **Reset** | "reset one-pager-composer to defaults" | `wipe_skill_config(workspace_root, "one-pager-composer")` → next fire is a first-fire again. |

**The first-run block (footer — one-pager-composer ends in a chat link to the .docx, not a widget):**

> *First time making a one-pager for you. I set 2 defaults: **formal tone (written for outside
> readers)** · **signed footer**. Say "tune my one-pagers" to change either, or just tell me
> ("internal/direct" / "unsigned").*

The footer renders exactly once ever (`is_configured` gate).

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "keep one-pagers internal/direct" / "less formal" | `register = internal_direct` |
| "make one-pagers external/formal" | `register = external_formal` |
| "leave one-pagers unsigned" / "no sign-off" | `signature = unsigned` |
| "sign my one-pagers" | `signature = signed` |

After applying: `save_skill_config(..., is_reconfigure=True)` + re-produce + confirm in one line.

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

**Deliverable link (CONTRACT Rule 3 — H2 heading link, LAST in the turn):** surface the .docx via `chat_output_renderer.doc_headline_link(label, brief_path.get_brief_artifact_url(absolute_path))` as the final line of the chat response — after the widget/summary and Sources, never interspliced mid-body, never a plain-text path, never a hand-built `computer://` URL.

Every one-pager uses this skeleton. No exceptions — consistency is the product.

> **Executive Output Standard (EXEC1, v3.20.0+) — decision-forward.** Per `shared/EXECUTIVE_OUTPUT_STANDARD.md`: the **Headline is the exec-header VERDICT** (it already IS the conclusion — the one-pager is the standard's model for "headline = conclusion") and the **Recommendation leads** (it moves directly under the header, before the Key Points / Supporting Data audit trail). **FS-13: the one-pager renders the VERDICT lead ONLY — `brief_writer` no longer renders the CHANGED / DECIDE / NEEDED eyebrow for `one_pager` (it is a brief-family scaffold that misframed the page and cost it its single-page fit). Do not compose those lines; a why-now belongs in the Recommendation, not an eyebrow.** `one_pager` is decision-shaped, so `make_brief` ENFORCES the ordering — a Recommendation-headed section appearing only at section index > 2 raises. **The Recommendation gains a decide-by date and a cost-of-delay line ONLY when the arithmetic traces to substrate** (via `quantify.money_time_tag` / a logged figure); date alone otherwise — NEVER an estimated cost-of-delay.

> **Exemplar anchor (SPEC OUT8).** Before composing, load the kind's structural exemplar — `exemplars.get_exemplar("one_pager", workspace_root)` (`shared/scripts/exemplars.py`) — and anchor STRUCTURE on it: section order, visual placement, proportions (the fixed skeleton below stays authoritative; the exemplar anchors layout within it). Workspace exemplar (`_hq/exemplars/one_pager/`) beats the shipped seed; `None` = compose on the skeleton below, unchanged. **Contract beats exemplar beats default** — an exemplar never licenses skipping the exec header, the ordering check, or the one-page cap, and it anchors structure, never facts: no name, number, or claim from the exemplar may appear in the page. After saving, run `exemplars.scan_docx_for_exemplar_tokens(docx_path, exemplar["text"])`; a finding means exemplar placeholder content leaked — fix the sections payload and re-save AT MOST ONCE (the visual-pass posture, warn-only). When the user gives structural feedback on a delivered one-pager ("make it like this", reorder/drop a section), capture it with `exemplars.append_structural_correction(workspace_root, kind="one_pager", direction=..., section=...)` — capture only; the exemplar itself updates exclusively through insight-generator's confirm-first proposals (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor").

1. **Headline → exec-header VERDICT** (1 line) — The single conclusion the reader should take away. Active voice, specific, no hedging. (Subhead's why-now folds into the header's CHANGED line.)
2. **Recommendation** — What the reader should do. One paragraph, specific and time-bound. *(EXEC1: leads the body, directly under the header.)* Gains a decide-by date always, and a cost-of-delay line when it traces to substrate ("every week of delay is ~$18K of exposure" — only when `quantify` returns it).
3. **Three Key Points** — Each one a tight paragraph, 2-4 sentences max. Lead with the claim, follow with the evidence. Bolded lead-in phrase. *(The audit trail for the recommendation above.)*
4. **Supporting Data** — 3-5 bullets with numbers, dates, sources. If data is estimated or unavailable, say so plainly.
5. **Footer** — Author, date, 1-line source note.

Total length: fits on one US Letter page with 1-inch margins, 11pt Calibri. If content is overflowing, cut — don't shrink type.

## How It Works

1. Parse the prompt for: topic, audience, any attached data, any attached context files.
2. If project context is available (user is in a client project folder), pull relevant session notes, PROJECT_CONTEXT.md, and recent deliverables for grounding.
3. If web research is needed and the user hasn't said "use my notes only," use the web research connector to pull 2-3 authoritative sources.
4. Draft using the skeleton above. Voice comes from the baked-in `## Voice Block` in this SKILL.md (Voice Protocol v3.0) — do not read external `VOICE_SAMPLES.md` files.
5. **Render via the canonical brief_writer (v3.13.8+ — Bug #53):** call `shared/scripts/brief_writer.py` `make_brief(brief_kind="one_pager", ...)`. Do NOT invoke the `docx` skill or hand-roll docx-js. brief_writer enforces canonical Calibri typography, navy heading hierarchy (Heading 1/2/3 per Bug #7), eyebrow label "ONE-PAGER", and runs the universal post-render leak scanner (Bug #57/#59/#54) automatically. **Visual grammar (FS-12 — MUST):** data-bearing sections (supporting data, metrics, comparisons) render through the `table` / `matrix` / `tiles` primitives, never a bullet wall; before the visual pass run `visual_gate.flag_zero_table_data_heavy(sections, "one_pager")` and restructure any section it names. Fewer, structured facts also protect the single-page fit.
6. Save to `[Current Project]/deliverables/` with filename `[Topic]_OnePager_[YYYY-MM-DD].docx`. If no current project context, save to `_hq/deliverables/` (matches the Writer Contract — this is the ONLY fallback location).
7. Return the file link + a 2-sentence summary of what's in it. Done.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "I made 2 calls: external-formal register · signed"
- Good: "I set 2 defaults: formal tone (written for outside readers) · signed footer"

**Output-contract gate (B3 — pre-save, before the voice gate).** `make_brief(brief_kind="one_pager", ...)` validates the structured `sections` against `shared/scripts/output_contract_validator.py` `RULES_BY_KIND["one_pager"]` BEFORE `Document()` is built (canonical order: contract → voice → render → leak scan): headline (the `title`) ≤25 words, Supporting Data 3-5 bullets, total 120-480 words, and the no-placeholder rule. The allowed form `[Figure needed — confirm before sending]` passes; every other placeholder (TBD, `[PLACEHOLDER]`, etc.) fails. On a blocking violation it raises `OutputContractError` (no file written). Read each violation's `section` + `fix_hint`, rewrite ONLY the failing sections — cut the headline, right-size the supporting-data bullets, or replace stray placeholder text with the allowed form or real data — and call `make_brief` again. Maximum 2 retries, then surface the failure plainly instead of shipping a substandard one-pager. **Sync rule: if you change the 6-part skeleton's counts or the headline cap here, change the matching entry in `output_contract_validator.py` `RULES_BY_KIND["one_pager"]` in the same commit.**

## File Naming

Format: `[Topic]_OnePager_[YYYY-MM-DD].docx`

Examples:
- `ChinaSupplyChain_OnePager_2026-04-20.docx`
- `Q3MarginDrop_OnePager_2026-04-20.docx`
- `AcquisitionTarget_Mercer_OnePager_2026-04-20.docx`

## Formatting Standards (handled by brief_writer canonical path)

The canonical `brief_writer.make_brief(brief_kind="one_pager", ...)` path already enforces:

- US Letter, 1-inch margins
- Body, headline, and section-label fonts + colors resolve through the brand theme (`shared/scripts/brand.py` — upgraded quiet-professional defaults; per-client override via the `brand` object in entities.json). Never hand-specify a color
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
- Never fabricate numbers to fill the supporting-data section. Mark `[Figure needed — confirm before sending]` and keep moving.
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

### Anti-examples (bad → good)

Apply the Universal writing standards in `shared/VOICE_CALIBRATION.md` (structure, specificity, floors — they do not override this skill's Voice Block). Each one-pager element has a failure mode; the contrast is the test.

**Headline** — bad states a topic or asks a question; good states the conclusion.
```
BAD:  A review of our Q3 margin performance.
GOOD: Q3 margin dropped 17%. Three supplier concentration bets are why.
```

**Key point** — bad leans on adjectives; good leads with the claim, then the number.
```
BAD:  Supplier concentration is a significant and meaningful risk that
      could materially impact our margins going forward.
GOOD: Supplier concentration is the headline risk. 62% of Q3 hardware
      COGS came from three suppliers; two raised prices 9-14% in September.
```

**Supporting data** — bad is a vague assertion; good is a number with a date and source.
```
BAD:  Margins have been under pressure recently.
GOOD: Gross margin fell from 41% to 34% between Q2 and Q3 (internal P&L, Oct 3).
```

**Recommendation** — bad is a direction with no owner or date; good names the action, the owner, the deadline.
```
BAD:  We should look into diversifying our supplier base soon.
GOOD: Dual-source the top three SKUs by August 15; Sam's team owns the
      shortlist by July 1.
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
- **Always render via `make_brief(brief_kind="one_pager", ...)`** (see the Deliverable Render Gate at the top). Do NOT hand-roll a `.docx` via the generic `docx` skill, and do NOT answer a one-pager request with a chat-only draft — those bypass the gates. Output markdown/inline text ONLY if the user explicitly says "just draft it in chat," and flag plainly that the quality and voice checks only run on the file version.
- **Check the audience.** A one-pager for the board reads differently than one for the sales team. If audience isn't stated and isn't obvious from project context, ask.

## What It Doesn't Do

- Multi-page reports — use `memo-writer` (recurring updates / strategy memos) or a full report workflow
- Decision frameworks / comparative tradeoffs — use `decision-memo-composer` (structured multi-option analysis); single-decision narrative capture is `memo-writer`'s decision doc
- Slide decks — use `pptx` or the slide-deck skill

## Template Pattern for Other Composers

This skill establishes the shared pattern for all output-layer skills:

- Parse → Ground (project/web) → Structure → Draft in voice → Render via the canonical `brief_writer.make_brief` chokepoint → Save → Return link
- Output skeletons are fixed — the value is consistency, not creativity
- Always render `.docx` deliverables through `brief_writer.make_brief` (GATE1) — never build files directly or via the generic `docx` skill
- Always save into `deliverables/` with the naming convention
- Always return a file link + 2-sentence summary, nothing more

## Connected Tools

- **`shared/scripts/brief_writer.py` `make_brief(brief_kind="one_pager", ...)`** — produces the Word file (the GATE1 chokepoint)
- **Session Notes / PROJECT_CONTEXT** — project grounding
- **Web research connector** — external data when needed
- **Voice Block** (baked into this SKILL.md) — user voice calibration (v3.0)

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Turn any topic, question, or pile of notes into a polished one-page executive brief in under 60 seconds. Produces a formatted .docx with headline, 3 key points, supporting data, and recommendation — saved to the project's deliverables/ folder, ready to send to a partner, customer, or board member. Use when the CEO says 'one-pager', 'one-pager on', 'write me a one-pager', 'make me a one-pager', 'I need a one-pager', 'need a one-pager', 'throw together a one-pager', 'single-page brief on', 'executive one-pager', 'turn these notes into a one-pager', 'one page summary', 'one page summary of', 'brief me on [topic] in one page'. Voice-calibrated via this skill's Voice Block per shared/VOICE_CALIBRATION.md. Also handles first-run personalization settings — use when the CEO says 'tune my one-pagers', 'tune one-pager-composer', 'show one-pager-composer settings', 'reset one-pager-composer to defaults'. DOES NOT fire on requests for multi-page reports / recurring updates / decision memos (use memo-writer) or slide decks (use pptx / slide-deck). This is the shortest-form output skill — pick it when the answer fits on one page.
