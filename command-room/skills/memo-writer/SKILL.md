---
name: memo-writer
description: "Draft internal memos, decision docs, scope docs, strategy notes, and recurring board / investor updates in the CEO's voice — structured, directive, written to persuade or align. Use when the CEO says 'write a memo on', 'memo on', 'draft a decision doc', 'decision doc', 'scope doc', 'scope doc for', 'strategy memo about', 'strategy memo', 'position paper', 'write up our thinking on', 'internal memo', 'board update', 'monthly CEO letter', 'quarterly investor update'. Produces a 1-3 page structured document saved to the project's deliverables folder. Runs voice calibration protocol per shared/VOICE_CALIBRATION.md with this skill's Voice Block. DOES NOT fire on 'one-pager' (that's one-pager-composer — shorter, more visual), 'email-only requests' (email-writer), 'decision memo on', 'decision memo for', 'tradeoff analysis', 'choosing between' (those go to decision-memo-composer — structured multi-option tradeoff, v3.8.0+; the decision doc trigger memo-writer owns is for single-decision narrative capture), or 'board pack' (that's board-pack-assembler — multi-page structured pack; the board update trigger memo-writer owns is for shorter freeform narrative)."
voice_block_last_refreshed: 2026-04-21
calibration_level: default
template_version: 1.0.0
---

# Memo Writer

**For:** CEOs who need to commit thinking to a shareable document — a decision rationale for the team, a scope doc that aligns a project, a strategy memo that frames a direction. Longer than an email, more structured than a Slack message, more textual than a one-pager.

## Skill Boundary

- **Use `memo-writer` for:** internal memos (team/board distribution), decision docs, scope docs, strategy notes, position papers.
- **Use `one-pager-composer` for:** shorter, more visual one-page briefs (headline + 3 key points + rec).
- **Use `email-writer` for:** anything that would be delivered as an email body.

## Writer Contract

Before writing, read `shared/WORKSPACE_API.md`.

**Primary writer for:**
- `[Project]/deliverables/memos/[YYYY-MM-DD]_[topic].docx` — final memo output. Per CONTRACT Rule 27 (no .md deliverables): the prior `.md` "markdown source for review" was retired in v3.7.0 because customers opening the redundant `.md` in Word saw raw markdown syntax. The `.docx` is the canonical deliverable.
- `_hq/voice/corrections-memo-writer.jsonl` — append on correction.

**Appends to:**
- `_hq/data/events.jsonl` — event type `memo_drafted` with `{topic, audience, memo_type, primary_thread_id, artifact_path, source_decision_ids[]}` where `memo_type ∈ {decision_doc, scope_doc, strategy_memo, position_paper, board_update, investor_update, ceo_letter}` (parsed from the trigger phrase).
- **(v3.7.1+) Auto-fires `decision-log` when `memo_type == "decision_doc"` OR the trigger phrase is "decision doc" / "decision memo".** The chained `decision-log` invocation writes the canonical `decision` event with the memo `.docx` path as the rationale link. Pre-v3.7.1 the user had to remember to log the decision separately after writing the memo, which they often forgot — the substrate ended up with memos describing decisions that were never logged. Auto-fire closes that gap.

**Reads (v3.7.1+ substrate enrichment):**
- `_hq/data/entities.json`, `events.jsonl` (as before).
- `_hq/data/events.jsonl` prior `decision` events on the topic — the memo prompt surfaces "decisions you've already taken on this" so the new memo doesn't re-litigate settled ground. Source seqs go into `source_decision_ids[]`.
- Relevant `[Project]/PROJECT_CONTEXT.md` and `PROJECT_BRAIN.md` for project-specific facts.
- This skill's Voice Block.
- `shared/VOICE_CALIBRATION.md`.
- Past memos in the project's memos folder for structural consistency.

---

## What It Does

Takes a topic + audience + angle and produces a structured memo that sounds like the CEO wrote it. Handles the common memo types with appropriate structure for each.

## How It Works

### Phase 1 — Memo type detection

Parse the input to identify the memo type — each has a different structure:

**Decision memo** — "I decided X. Here's why. Here's what's next."
Structure: Decision → Context → Options considered → Rationale → Implications → Next steps

**Scope memo** — "Here's what's in, here's what's out."
Structure: Objective → In scope → Out of scope → Success criteria → Timeline → Owners

**Strategy memo** — "Here's how we should think about X."
Structure: Question → Framing → Analysis → Recommendation → Risks → Open questions

**Position paper** — "Here's my view on X."
Structure: Thesis → Evidence → Counterarguments addressed → Conclusion

**Project update memo** — "Here's where we are."
Structure: Status → What's done → What's blocked → What's next → Ask

If type is unclear, ask ONE question: "Decision, scope, strategy, position, or update memo?"

### Phase 2 — Context harvest

- `PROJECT_CONTEXT.md` of the relevant project
- `PROJECT_BRAIN.md` — facts, people, decisions history
- Last 5 events in the project from `events.jsonl`
- Any referenced prior memos

### Phase 3 — Voice-calibrated draft (two-step)

#### Step 1 — Draft

Target length by type:
- **Decision memo:** 300-600 words
- **Scope memo:** 400-800 words (scope docs need precision)
- **Strategy memo:** 600-1000 words
- **Position paper:** 500-900 words
- **Project update memo:** 250-500 words

Memos use prose in paragraphs, NOT bullets-everywhere. The CEO's voice should carry the argument. Bullets appear only where lists are genuinely appropriate (success criteria, open questions, named items).

#### Step 2 — Critique

Apply universal banned-phrase list + memo-specific critique:
- Does the opening paragraph state the thesis/decision in the first 2 sentences? If not, rewrite.
- Is there any paragraph >150 words? Break it.
- Are there bullet lists where prose would carry the argument better? Convert.
- Does the close have a clear next step or ask? If not, add.
- Voice Block structural rules enforced.

### Phase 4 — Produce .docx via the canonical brief_writer (v3.13.8+ — Bug #53)

Render through `shared/scripts/brief_writer.py` `make_brief(brief_kind="memo", ...)`. **Do not invoke the docx skill or hand-roll python-docx.** brief_writer enforces:
- Canonical Calibri typography + navy heading hierarchy (Heading 1/2/3 styles per Bug #7 fix)
- Universal post-render leak scanner (Bug #57 + #59 + #54) — every memo runs through `docx_leak_scanner` automatically before save returns
- Eyebrow label "MEMO"

Use the table / matrix primitives (v3.13.8+) for any tabular content rather than synthesizing bullets:

```python
from brief_writer import make_brief
make_brief(
    output_path,
    brief_kind="memo",
    title="<topic>",
    subtitle="<audience or framing>",
    sections=[
        {"heading": "Decision", "body": "<one-paragraph statement>"},
        {"heading": "Context", "body": "<...>"},
        {"heading": "Options considered", "bullets": ["...", "..."]},
        {"heading": "Tradeoffs", "table": {"headers": ["Option", "Cost", "Speed"], "rows": [...]}},
        {"heading": "Rationale", "body": "..."},
        {"heading": "Next steps", "bullets": ["...", "..."]},
    ],
)
```

**Do NOT save a markdown source alongside the .docx** — per CONTRACT.md Rule 27, .docx is the canonical deliverable and a parallel `.md` would surface as a redundant file in the customer's folder. The .docx itself is the version-of-record; regeneration uses the source skill + the current substrate.

### Phase 5 — Log event + correction detection

---

## Voice Block

**Last refreshed:** 2026-04-21
**Calibration level:** default
**Sample count:** 0 (uncalibrated)

### Sentence cadence
- Typical length: 15-25 words (longer than email — memo has more room)
- Maximum before breaking: 35 words
- Short-punch frequency: occasional, especially for emphasis at paragraph starts

### Openers
- Preferred: state the thesis. "We should X." "I've decided Y." "Here's the question we need to answer."
- Avoided: "This memo outlines...", "The purpose of this memo is to...", "As we discussed..."
- Never use: "I wanted to write this memo because...", "Hope this memo finds you well"

### Vocabulary
- Uses: direct claims, specific verbs, concrete numbers
- Avoids: "leverage", "synergies", "going forward", "stakeholders" (use actual names), "ecosystem", "holistic"
- Domain-specific: domain terms of the relevant project

### Punctuation
- Em-dashes: occasional for emphasis
- Semicolons: rare but OK for paired clauses
- Parentheticals: occasional (memos tolerate more parentheticals than emails)
- Colons: acceptable before lists or definitions

### Structure
- Lead with: thesis or decision in first 2 sentences of the memo.
- Paragraph length: medium (60-120 words typical). No walls of text.
- Bullet use: reserved for lists of named items, success criteria, questions. Never for the core argument.
- Headers: H2 for major sections. H3 only if needed for a nested subsection.
- Sign-off: none — memos don't need a closing signature in most cases.

### Tone markers
- Register: formal-professional, still direct
- Self-reference: "I" and "we" both used; "I" for personal positions, "we" for collective decisions
- Hedging: moderate (memos acknowledge uncertainty better than emails do) — but commit to a position at the end
- Authority: confident. The CEO writes memos to align people; hedging undermines alignment.

### Taboos (per-skill)
- Never: "it could be argued that", "some might say", "going forward", "move the needle"
- OK despite being on universal list: "stakeholders" (if specific group is named), longer sentences than email allows

### Examples

**Example 1 — Decision memo opener:**
```
# Pricing Decision: Command Room Product Layer

We're pricing the Command Room product at $1,250 one-time, separate from
the customization tier. This memo explains why this structure beats the
alternatives and what it commits us to.

The reasoning is three-fold...
```

**Example 2 — Scope memo opener:**
```
# Chalette Customization Scope — v1

The $10K customization tier includes six specific workstreams. Anything
outside these six moves to the monthly retainer. This memo exists so
both parties — Chalette and the client — know the line before work
starts.

## In scope...
```

**Example 3 — Strategy memo close:**
```
The play isn't to be cheaper than the incumbents. The play is to be the
only option that actually calibrates to how the CEO works. That's a
moat you can't copy with features.

Next step: build the calibration service into a productized offering
by end of Q2. Everything else waits on that.
```

---

## Staleness

Same universal rules.

## Output quality bar

Memos are the highest-stakes writing skill's output — they frequently go to boards, investors, executive teams. Voice calibration at the $10K tier matters most for this skill. Uncalibrated default: ~55% quality (usable but generic). Calibrated: ~90%.
