# Voice Calibration — v3.0 (v2.7.1 architecture)

**Status:** Mandatory protocol for every writing skill. This file is the universal contract — it contains NO CEO-specific voice content. Voice lives inside each writing skill as a per-skill voice block.

**Supersedes:** v2.1 shared-VOICE_SAMPLES.md model. That approach was structurally fragile (composers drifted, no enforcement, no feedback loop). v3.0 bakes voice into each skill and limits this shared file to protocol mechanics.

**Applies to:** every writing skill that produces CEO-facing output — `email-writer`, `memo-writer`, `follow-up-ritual`, `one-pager-composer`, `inbox-triage` (when drafting replies), `decision-memo-composer`, `board-pack-assembler`, `intro-broker`, and any future writing skills.

---

## Core architecture

Two tiers. Non-negotiable.

### Tier 1 — This file (shared, universal, ships with public plugin)

Contains:
1. The two-step draft-then-critique protocol every writing skill must implement.
2. The banned-phrase list of universal LLM tells.
3. The correction-log schema.
4. Staleness rules.
5. The voice regression harness spec (lives in the chalette internal plugin as of v3.9.0 — Matthew-only tooling, not customer-facing).

Contains NOTHING client-specific. Never references a CEO's actual voice.

### Tier 2 — Per-skill voice block (baked into each writing skill's SKILL.md)

Every writing skill has a section titled `## Voice Block` in its SKILL.md. This block contains the CEO's domain-specific voice for that skill. For the public plugin, the voice block is populated with a generic professional default. For client installs (the $10K customization), Chalette replaces the generic voice block with a calibrated one extracted from the client's actual writing samples.

### The rail boundary — voice owns WORDS, exemplars own LAYOUT (SPEC OUT8)

This rail calibrates phrasing, vocabulary, and tone. Document STRUCTURE — section order, visual placement, target length — is calibrated by the separate exemplar rail (`shared/scripts/exemplars.py`, seeds under `shared/exemplars/`, learned overrides under `_hq/exemplars/`; contract in `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor"). The two rails stay disjoint: a Voice Block never prescribes section order; an exemplar never prescribes phrasing. A correction that is purely structural (reordered sections, dropped a section, tiles-vs-prose) belongs in `_hq/exemplars/corrections-<kind>.jsonl`, not in a voice correction log.

---

## The two-step protocol

Every writing skill implements this as a prompt chain, not as a single-shot draft:

### Step 1 — Draft

Draft the output using:
- The task input (what to write, to whom, about what)
- The per-skill Voice Block (cadence, openers, vocabulary, punctuation, taboos)
- Recipient context from `entities.json` if available (role, org, prior interaction style)

### Step 2 — Critique

Re-read the draft. Score it against the Voice Block dimensions. Ask:

- Does the sentence cadence match the Voice Block?
- Does any sentence start with a banned opener?
- Does the output contain any phrase from the universal banned-phrase list below?
- Does any paragraph break the voice block's structural rules (e.g., lead-with-conclusion, no buildup)?
- Is the overall register appropriate for the domain (email ≠ board update ≠ Slack)?

If any check fails, rewrite that section. Repeat until all checks pass. Then return.

This two-step is a prompt discipline, not additional code. The skill's instruction prompt explicitly walks through both steps. Do not return a draft without completing the critique pass.

---

## Universal banned-phrase list

Strip or rewrite these phrases before returning any output. These are LLM tells — generic-assistant language that instantly breaks the illusion that the CEO wrote the draft.

### Openers to never use
- "I'd be happy to..."
- "I'd love to..."
- "Happy to help..."
- "Great question."
- "That's a great point."
- "Thanks for reaching out." (unless truly apt)

### Filler phrases to strip
- "Let me know if..."
- "Feel free to..."
- "I hope this helps."
- "Hope this finds you well." (unless the CEO demonstrably uses it)
- "Please don't hesitate to..."
- "Circling back on..."
- "I wanted to circle back..."
- "Just wanted to check in..."
- "Touching base..."
- "As per my last email..."

### Preambles to strip
- "Here's a draft."
- "Here's what I came up with."
- "Below is a draft for your review."
- "I've put together..."

### Structural tells
- Tri-colon constructions ("First...: Second...: Third...:") where prose fits.
- Bulleted lists inserted into email/Slack output where the CEO would have used prose.
- **No dashes as punctuation in body prose** (FB-16 — a product-level ban, default-on for every client): no em dash, no en dash, no spaced hyphen. Use a comma, a colon, or rewrite. Hyphenated compounds ("follow-up", "check-in") stay legal, and the standalone "— Name" sign-off is exempt. A client whose calibrated voice keeps dashes overrides via `ban_dashes=False` or a Voice Block `allow_phrases` entry. This REPLACES the old warn-only "more than 2 em-dashes per paragraph" rule, which scored a single "we shipped — fast" as `pass` and let it slip a quick-drafted email body through the gate. Now a FAIL, one finding per occurrence.
- Over-hedging ("I think it might be possible that perhaps...") — commit or cut.

### Closers to never use
- "Best regards" if the CEO's samples don't use it.
- "Warm regards" same.
- "Looking forward to hearing back" unless it's in the voice block.

### Subject lines (v4.6.1 S3 — the gate covers subjects, not just bodies)

- **No dashes as punctuation in email subject lines** — no em dash, no en dash, no spaced hyphen. Use a comma, a colon, or rewrite ("Q2 deck — status" → "Q2 deck: status"). Hyphenated compounds ("follow-up", "check-in") stay legal. F-47 P2d and F-53 shipped em-dash subjects twice in one dogfood day. Subjects have NO pile-up allowance — one dash fails. As of FB-16 the body gate enforces the same ban on body prose, so this is the BRAND_VOICE hard rule on every generated surface, not just the subject line the body gate did not scan.
- The banned phrases and vocabulary words above apply to subjects too — a subject that leads with "Circling back" or "leverage" is still the voice.
- Enforced by `voice_tell_detector.scan_subject` (`--context subject` on the CLI). Every surface that mints a subject — email-writer Phase 3.5, the commitments-chat chase drafts, follow-up-ritual — runs it before showing the draft.

### Vocabulary (one shared list, one owner)

The corporate-vocabulary words neither gate lets through — currently `ecosystem`, `synergy`, `leverage`, `holistic`, `stakeholder` — live in **`shared/scripts/vocabulary_policy.py`**, the single owner. Both gates read it: the leak gate (`docx_leak_scanner`) hard-fails a docx save on them; the voice gate (`voice_tell_detector`) fails drafts on them. Never add a vocabulary word to a gate's local list — S3 exists because the two lists were disjoint and "leverage" was blocked in a docx while leading an email the same day (F-53 P3a). Client carve-out: a Voice Block Taboo the CEO demonstrably uses feeds through `allow_phrases` and is never blocked.

The client-specific voice block MAY override individual items in this list if the CEO demonstrably uses them. The override must be explicit (e.g., "Voice Block: CEO uses 'Hope this finds you well' — do not strip").

**Sync rule (B2):** the canonical machine-readable encoding of this banned-phrase list lives in `shared/scripts/voice_tell_detector.py` (`_FAIL_PHRASES`), the save-time gate that enforces it. The two MUST change together — add a phrase here and add the matching rule there. `tests/run_voice_tell_detector_test.py` asserts the detector's fail-rule count is ≥ this list's bullet count, so a one-sided edit fails the battery loudly. The override carve-out above maps to the detector's `allow_phrases` hook (a Voice Block Taboo a client demonstrably uses is fed through and never hard-blocked). Vocabulary WORDS are the exception: they live in `vocabulary_policy.py` (one owner, both gates) — not in `_FAIL_PHRASES` and not here.

---

## Correction log schema

Every writing skill maintains its own correction log at:

```
_hq/voice/corrections-[skill-name].jsonl
```

Example paths:
- `_hq/voice/corrections-email-writer.jsonl`
- `_hq/voice/corrections-memo-writer.jsonl`
- `_hq/voice/corrections-one-pager-composer.jsonl`

### Row format

```json
{
  "timestamp": "2026-04-21T14:33:00Z",
  "skill": "email-writer",
  "domain": "email-short-external",
  "recipient_id": "person_012",
  "original_draft": "I wanted to circle back on our conversation yesterday.",
  "corrected_by_user": "Following up on yesterday.",
  "correction_type": "phrasing",
  "notes": "strip 'wanted to', strip 'circle back', shorten"
}
```

### When to append

Any time the user edits the skill's output before using it. Detected by comparing the skill's return value to what ends up in the downstream artifact (email draft, Slack message, etc.) at end-session.

If the skill can't detect the correction automatically (user edits offline), the user can append manually via a "log correction" command that prompts for the before/after.

### Never rewrite rows

The corrections log is append-only. Prior rows are never edited. If a row is superseded by a later decision, append a new row with `supersedes_timestamp: [earlier-row]`.

---

## Correction batching and voice-block refresh

Weekly or monthly (owner: `insight-generator` weekly, Chalette monthly for client installs):

1. Read the correction log for each writing skill.
2. Group corrections by `correction_type` (phrasing / structure / vocabulary / tone).
3. Identify patterns — e.g., "user corrected 7 instances of 'circle back' to 'following up on' in the last month."
4. Propose voice-block updates: "Add to banned openers: 'wanted to circle back'. Add to preferred openers: 'Following up on [X]'."
5. Present to user for approval.
6. On approval, write the refreshed block to the **customer-side override store** `_hq/voice/voice-block-<skill>.md` (NOT the SKILL.md — the plugin directory is a managed clone overwritten on every update, so a block written into SKILL.md is lost on the next install). The override supersedes the baked-in `## Voice Block` **section-by-section** (a section present in the override replaces the same-named default; absent sections fall through to the SKILL.md default). Append a `voice_block_updated` event. Implementation: `shared/scripts/voice_corrections.py` `write_voice_block_override`.
7. For a universal LLM tell (same pattern across 3+ skills), Chalette MAY also promote it plugin-side into this file's banned list and push a new private-plugin version — but the per-customer calibration always lives in the workspace override, never requiring a plugin push.

### Detection + the override store (B1 — how the loop actually runs)

The corrections are detected at two points, classified deterministically, and appended by `shared/scripts/voice_corrections.py`:

1. **Widget-edit (synchronous):** apply-choices Step 3d snapshots the canonical body (`_hq/voice/draft-snapshots.jsonl`) at send/draft time, and when the user's `input.body` differs it runs `diff_and_classify` + `append_correction` immediately. Highest-fidelity signal.
2. **Sent-mail (asynchronous):** reconcile-sent step 3b runs `reconcile_sent_against_snapshots` over the already-fetched Sent batch, matching by `gmail_message_id` then by recipient + normalized subject + a 7-day window.

The monthly batch is **insight-generator Pass 11**. **Every writing skill, at draft time, reads its `_hq/voice/voice-block-<skill>.md` override (if present) and applies it over the baked-in block per step 6 above** — this is the customer-side write target that makes calibration take effect without a plugin push. Draft snapshots store real body text (workspace-private, same class as transcripts) and are pruned by `cleanup`.

### Universal pattern promotion

If the same correction pattern appears across 3+ writing skills, it's a universal LLM tell. Add it to the banned-phrase list in THIS file. All skills get the benefit at their next refresh.

---

## Staleness rules

A Voice Block is stale if ANY of:

1. The block's `Last refreshed:` date is >12 months old. **Read the `_hq/voice/voice-block-<skill>.md` override's `Last refreshed:` header first when it exists; fall back to the SKILL.md `voice_block_last_refreshed` frontmatter otherwise.**
2. The skill's corrections log has >20 unreviewed corrections accumulated since last refresh ("unreviewed" = rows with `timestamp` after the last `voice_calibration_review` event's `reviewed_through[skill]`; compute via `voice_corrections.unreviewed_counts`).

Stale skills emit a notice at the top of their output:

```
⚠️ Voice Block may be stale. Last refreshed: [date]. Operator: rerun voice calibration when convenient (corrections corpus drives the next refresh).
```

Staleness does not block output — the skill still produces a draft, just with a notice.

---

## Voice regression harness (Matthew-only, chalette plugin)

The monthly voice-regression harness (`voice-test` skill) was retired from this plugin in v3.9.0 and moved to the chalette internal plugin (`chaletteholdings/chalette` v0.5.0+). It is Matthew-only — used during plugin development to catch voice drift before it reaches customer-facing output. Customers do not run it.

For customer installs, the staleness check above (date + correction count) is the trigger to re-run voice calibration. The corrections corpus accumulated in `_hq/voice/corrections-*.jsonl` is the canonical input.

### Refresh trigger

When the staleness check fires (or the operator explicitly calls for a refresh):
1. Read the skill's correction log.
2. Run correction batching (above).
3. Propose voice-block update.
4. On CEO approval, update SKILL.md and continue.

---

## Per-skill voice block template

Every writing skill's SKILL.md contains a `## Voice Block` section with this structure:

```markdown
## Voice Block

**Last refreshed:** YYYY-MM-DD
**Calibration level:** [default | calibrated]
**Sample count:** N

### Sentence cadence
- Typical length: X-Y words
- Maximum before breaking: Z words
- Short-punch frequency: [rare | occasional | common]

### Openers
- Preferred: [list]
- Avoided: [list]
- Never use: [list]

### Vocabulary
- Uses: [list of distinctive words/phrases]
- Avoids: [list]
- Domain-specific terms: [list]

### Punctuation
- Em-dashes: [rare | occasional | frequent]
- Semicolons: [rare | occasional | frequent]
- Parentheticals: [rare | occasional | frequent]
- Ellipses: [rare | occasional | frequent]

### Structure
- Lead with: [conclusion | context | hook | other]
- Paragraph length: [short | medium | long]
- Bullet use: [avoided | acceptable in specific cases | preferred]

### Tone markers
- Register: [formal | professional | casual | blunt | warm]
- Self-reference: [first-person frequent | first-person rare | third-person]
- Hedging: [direct | moderate hedging | heavy hedging]

### Taboos (per-skill overrides to universal list)
- Never: [list]
- OK despite being on universal list: [list with justification]

### Examples
[2-3 short canonical examples of the CEO's writing in this domain]
```

### Defaults for uncalibrated installs

When a writing skill ships with the public plugin (no client calibration yet), the Voice Block uses a "generic professional" default:

- Cadence: 12-20 words typical, max 30.
- Openers: lead with purpose. Avoid "I hope", "Happy to", "Great question".
- Vocabulary: neutral-professional. Avoid "leverage", "synergies", "going forward".
- Punctuation: em-dashes rare, semicolons rare.
- Structure: lead with conclusion. Short paragraphs.
- Register: professional, direct, no hedging.
- Bullets: acceptable for lists of 3+ items, avoided for flowing prose.

This default works at ~60% quality — the product still functions on day 1 without calibration. The $10K Chalette calibration lifts this to ~90%.

---

## Plain-language glossary (PL 2026-07-02 — customer-visible strings, ALL skills)

Internal vocabulary never appears in anything the CEO reads — chat copy, footers, briefs,
document bodies, button labels. Enforced by `tests/run_pl_banned_words_test.py` over the
customer-verbatim blockquotes (ship-gate guard G5 extends the same list to all extracted
customer strings). Say the right-hand column instead:

| Never (internal) | Say instead |
|---|---|
| substrate | your workspace / what I've captured about your business |
| fire / fires / re-fire | runs / kicks in / sends when you click |
| widget | the buttons / the cards (or just describe the action) |
| orchestrator, taskId | scheduled chat (by its display name) |
| connector | the tool by name ("your email", "your calendar", "Slack") |
| render(s) | show / appear |
| artifact | the document / the file (by its title) |
| thread (as project), workstream | project |
| person record / entity record | contact / profile (or the person's name) |
| view(s) (as data-layer noun) | list / page (or the thing's name) |
| cooldown | "I'll wait N days before bringing this up again" |
| people graph / voice corpus / engagement edge | your contacts / your writing style / (describe the signal) |
| buffered | saved / noted |
| version numbers (v3.x) | nothing — versions live in verify diagnostics + changelogs ONLY |
| raw paths (events.jsonl, _hq/…) | nothing — describe what the customer sees. (CLAUDE.md / BUSINESS_CONTEXT.md are customer-edited files and MAY be named in prose about editing them.) |
| snake_case enums / config keys | the plain-English meaning ("paused" not `status_paused`) |
| confidence decimals "(0.62)" | likely / not sure / strong match |

Two register rules that ride with the glossary: command hints are **"Say X"** (never "Run X" /
"type X") and advertise **natural trigger forms** ("weekly recap", "tune the dormant scan") —
verified to route — never hyphenated skill IDs. The model copies examples over rules: any
verbatim example block in a SKILL.md must itself pass this table (ban-in-rules + present-in-
examples is worse than no rule).

## Universal writing standards (all composer skills)

PRECEDENCE: these govern structure, specificity, and floors, and override a
skill's GENERIC default instructions. A client's calibrated per-skill Voice
Block still wins on voice, tone, openers, and taboos (per VOICE_CALIBRATION.md)
— never override a calibrated voice with these.

STAKES: Before drafting, state internally WHO reads this and WHAT decision or
action it drives. If you cannot answer both, ask the user one question first.
Register follows audience: board/investor = conclusion first, tightest language;
internal = more exploratory; customer-facing = warmer, more context on why.

SPECIFICITY: Replace every abstract claim with a concrete instance.
  BAD:  "Progress on the project" / "strong interest"
  GOOD: "Sent the revised spec to Sam on May 18 covering the three API concerns
         from the May 5 call" / "three inbound demos booked this week"
When tempted to write significant/meaningful/strong/robust — delete the word
and cite the evidence instead.

CRITIQUE: Step-2 critiques use the skill's named checklist (binary, countable
checks: opening states an action? any paragraph >150 words? banned phrases?).
"Review for quality" is not a critique step.

FLOORS: Content floors are counts and required elements, never adjectives.
Verify by counting before returning. A floor without a count is not a floor.

NO PADDING: A section with no source material is omitted entirely — never
"(TBD)", never filler, never a restatement. A missing section is honest;
a padded one erodes trust.

---

## Writer contract for writing skills

Every writing skill:

1. **Reads** this file (protocol) + its own SKILL.md Voice Block (voice).
2. **Implements** the two-step draft-then-critique protocol.
3. **Applies** the universal banned-phrase list before returning output.
4. **Appends** corrections to `_hq/voice/corrections-[skill-name].jsonl` when detected.
5. **Emits** staleness notice if applicable.
6. **Never writes** to its own SKILL.md (only `insight-generator` or Chalette refresh updates voice blocks).

---

## Chalette calibration workflow (client-facing service, not shipped)

Chalette internal SOP. Lives in `_chalette-hq/sops/VOICE_CALIBRATION_SOP.md` (not in public plugin).

1. **Sample collection** — pull 20+ writing samples from client's Gmail, Drive, Slack. Label each by domain (email-short, email-long, slack, board-update, memo, follow-up).
2. **Voice extraction** — for each domain, run the extraction prompt chain (or interview-assisted extraction for unusual voices). Produce a Voice Block per skill.
3. **Skill generation** — inject Voice Blocks into the client's private plugin (`chalette-[client]/skills/[writer-skill]/SKILL.md`).
4. **Iteration pass** — for each skill, invoke on 3 real scenarios. Capture corrections. Patch Voice Block. Re-test.
5. **Push** — publish the private plugin, client installs via marketplace.
6. **Monthly refresh** — during the retainer call, pull corrections logs, batch into Voice Block updates, push new plugin version.

Budget: 5 hours for initial calibration, 1 hour per month for refresh.

---

## Summary — what changed from v2.1

- **Killed** shared `_hq/VOICE_SAMPLES.md` as the source of CEO voice. Voice is now per-skill.
- **Killed** composer-level voice extraction on every invocation. Extraction happens once at calibration, gets baked into the Voice Block, and only refreshes on correction-driven triggers.
- **Added** the two-step draft-then-critique protocol as mandatory.
- **Added** the universal banned-phrase list.
- **Added** per-skill correction logs.
- **Added** the voice regression harness (retired from this plugin v3.9.0 — moved to chalette internal for plugin-developer use).
- **Added** staleness rules with visible notices.
- **Added** the Chalette calibration service workflow (lives in `_chalette-hq/`, referenced here).

The net effect: voice becomes a versioned artifact per writing skill, with a feedback loop that actually drives improvement, instead of a passive file that hoped the LLM would do the right thing.
