---
name: memo-writer
description: "Draft internal memos, decision docs, scope docs, strategy notes, position papers, and recurring board or investor updates in the CEO's voice — structured, directive, written to persuade or align. Fires on: 'memo on [topic]', 'decision doc for [topic]', 'scope doc for [topic]', 'strategy memo about [topic]', 'write up our thinking on [topic]', 'position paper on [topic]', 'board update', 'investor update', 'commitment forensics', plus 'tune memo-writer' and 'customize memo-writer'. Output is a voice-calibrated .docx with a mandatory ask/close block. Does NOT fire on 'decision memo on X' (decision-memo-composer — weighted tradeoff between options), 'one-pager' (one-pager-composer), 'build the board pack' (board-pack-assembler), or email drafting (email-writer). Full trigger family and memo-type table: Routing section in the body."

voice_block_last_refreshed: 2026-04-21
calibration_level: default
template_version: 1.0.0
---

**Customer voice-block override (B1):** before drafting, read `_hq/voice/voice-block-memo-writer.md` if it exists — it supersedes this SKILL.md's `## Voice Block` section-by-section (override sections replace same-named defaults; absent sections fall through). The universal banned-phrase list still applies except where the override's Taboos explicitly carve out an item. Staleness reads the override's `Last refreshed:` first.

# Memo Writer

**For:** CEOs who need to commit thinking to a shareable document — a decision rationale for the team, a scope doc that aligns a project, a strategy memo that frames a direction. Longer than an email, more structured than a Slack message, more textual than a one-pager.

## Deliverable Render Gate (GATE1 — MUST, v3.20.x)

This skill produces a `.docx` deliverable. It MUST be produced through the canonical chokepoint — no exceptions:

- **Render ONLY via `shared/scripts/brief_writer.py` `make_brief(brief_kind="memo", ...)`** (Phase 4 below). That single call runs the output-contract gate (B3), the voice-tell gate (B2), and the post-render leak scan, in that order, BEFORE the file is written.
- **NEVER hand-roll a `.docx`** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship substandard, voice-violating, or PII-leaking documents (the v3.20.0 failure mode).
- **NEVER answer a deliverable request with a chat-only draft.** A memo request produces the rendered file through `make_brief`. Only if the user explicitly says "draft it in chat, don't make a file" do you skip the file — and then flag it plainly: "the quality and voice checks only run on the file version."
- **Detectability:** `make_brief` emits a `gate_ran` audit event recording which gates ran. A memo fire that yields a document with NO `gate_ran` event for that turn is a flagged bypass. Pass `workspace_root` to `make_brief` so the event lands in substrate.
- **Visual pass (SPEC OUT2 §3, after every save):** run the render-then-critique pass per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass" — call `shared/scripts/visual_gate.py` `render_preview(<saved path>)`, LOOK at the returned page images against the 7-item checklist (orphaned heading at a page break · empty/placeholder tile · table overflow/wrap damage · cramped spacing · header/footer intact · brand palette applied · chart unreadable / overplotted), fix the sections payload + re-save AT MOST ONCE, then log `visual_gate.log_visual_gate(WORKSPACE_ROOT, doc, rendered, findings, fixed)` either way. `None` from the ladder = no renderer on this machine — log `rendered: false` with a `skipped_reason` and proceed exactly as before (warn-only forever: a finding never refuses a save, and the pass never loops).

If anything below seems to contradict this gate, THIS GATE WINS.

## Skill Boundary (v2.1)

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
- **(v3.7.1+) Auto-fires `decision-log` when `memo_type == "decision_doc"` OR the trigger phrase is "decision doc".** The chained `decision-log` invocation writes the canonical `decision` event with the memo `.docx` path as the rationale link. Pre-v3.7.1 the user had to remember to log the decision separately after writing the memo, which they often forgot — the substrate ended up with memos describing decisions that were never logged. Auto-fire closes that gap.

**Reads (v3.7.1+ substrate enrichment):** All `events.jsonl` reads go through ONE org-scoped load — **read via the org-scoped reader, never a raw load** (PGUARD2 — the memo's audience includes external readers): `from events_io import load_events_org_scoped; org_events, skipped = load_events_org_scoped(workspace_root)`, then filter by `type` at the call site. The reader applies the account-scope mask and drops personal-lane rows by design.
- `_hq/data/entities.json`, `events.jsonl` (as before — events via the org-scoped load above).
- `_hq/data/events.jsonl` prior `decision` events on the topic (from the org-scoped load) — the memo prompt surfaces "decisions you've already taken on this" so the new memo doesn't re-litigate settled ground. Source seqs go into `source_decision_ids[]`.
- Relevant `[Project]/PROJECT_CONTEXT.md` and `PROJECT_BRAIN.md` for project-specific facts.
- This skill's Voice Block.
- `shared/VOICE_CALIBRATION.md`.
- `_hq/custom/memo-writer.md` — SCL1 standing customization preferences, via `skill_custom_writer.load_directives` (absent → defaults). See the Customization (SCL1) section below.
- Past memos in the project's memos folder for structural consistency.

---

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). Both
decisions are **show-then-tune (STT)** — the memo is produced first, then one-tap changes are
offered. Read config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22) — the placeholder form
# silently no-opped. Bash preamble: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT per the preamble above
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "register": "external_formal",  # external_formal | internal_direct
    "signature": "signed",          # signed (sign-off block) | unsigned
}
cfg = get_config(workspace_root, "memo-writer", DEFAULTS)
```

`register` sets the default tone (the Voice Block still governs voice; this picks formal-external vs
direct-internal framing). `signature` controls whether the memo closes with a sign-off block.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "write a memo on…" | produce the memo with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "memo-writer", DEFAULTS)` BEFORE rendering, then append the first-run footer after the .docx link. |
| **Show settings** | "show memo-writer settings" | render current config in plain English; no memo. |
| **Tune** | "tune memo-writer" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → re-produce. |
| **Reset** | "reset memo-writer to defaults" | `wipe_skill_config(workspace_root, "memo-writer")` → next fire is a first-fire again. |

**The first-run block (footer — memo-writer ends in a chat link to the .docx, not a widget):**

> *First time writing a memo for you. I set 2 defaults: **formal tone (for outside readers)** ·
> **signed close**. Say "tune my memos" to change either, or just tell me ("keep memos
> internal/direct" / "leave them unsigned").*

The footer renders exactly once ever (`is_configured` gate).

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "keep memos internal/direct" / "less formal" | `register = internal_direct` |
| "make memos external/formal" | `register = external_formal` |
| "leave memos unsigned" / "no sign-off" | `signature = unsigned` |
| "sign my memos" | `signature = signed` |

After applying: `save_skill_config(..., is_reconfigure=True)` + re-produce + confirm in one line.

## What It Does

Takes a topic + audience + angle and produces a structured memo that sounds like the CEO wrote it. Handles the common memo types with appropriate structure for each.

## How It Works

### Phase 1 — Memo type detection

Parse the input to identify the memo type — each has a different structure:

**Decision doc** — "I decided X. Here's why. Here's what's next."
Structure: Decision → Context → Options considered → Rationale → Implications → Next steps

**Scope memo** — "Here's what's in, here's what's out."
Structure: Objective → In scope → Out of scope → Success criteria → Timeline → Owners

**Strategy memo** — "Here's how we should think about X."
Structure: Question → **Recommendation** → Framing → Analysis → Risks → Open questions

**Position paper** — "Here's my view on X."
Structure: Thesis → Evidence → Counterarguments addressed → Conclusion

**Project update memo** — "Here's where we are."
Structure: Status → What's done → What's blocked → What's next → Ask

> **EXEC1 ordering (v3.20.0+, enforced):** `memo` is decision-shaped, so `make_brief` raises if a Recommendation/Decision/Thesis-headed section first appears at section index > 2 — analysis exists to AUDIT the recommendation, not defer it. The Decision doc (Decision-led), Position paper (Thesis-led), and Project update (no rec heading) already comply; the Strategy memo's Recommendation moves ABOVE Analysis (shown above). The recommendation is ALSO surfaced as the `exec_header.verdict`.

If type is unclear, ask ONE question: "Is this a decision doc, a scope doc, a strategy memo, a position paper, or a project update?"

### Phase 2 — Context harvest

- `PROJECT_CONTEXT.md` of the relevant project
- `PROJECT_BRAIN.md` — facts, people, decisions history
- Last 5 events in the project from `events.jsonl` — from the Reads section's org-scoped load (`load_events_org_scoped`), never a raw read
- Any referenced prior memos

### Phase 3 — Voice-calibrated draft (two-step)

#### Step 1 — Draft

Apply the Universal writing standards in `shared/VOICE_CALIBRATION.md` (structure, specificity, floors — they do not override this skill's Voice Block on voice/tone/openers/taboos).

Adjust register by memo_type BEFORE drafting:
- `board_update` / `investor_update`: the opening paragraph must stand alone as a 30-second read answering "if I read nothing else, what should I know?"
- `scope_doc` / `position_paper` (external): warm the tone one step — fewer formalisms, one concrete example, explain the why behind choices.
- `internal` memo: balance conclusion-first with the reasoning path.

Target length by type:
- **Decision doc:** 300-600 words
- **Scope memo:** 400-800 words (scope docs need precision)
- **Strategy memo:** 600-1000 words
- **Position paper:** 500-900 words
- **Project update memo:** 250-500 words

Memos use prose in paragraphs, NOT bullets-everywhere. The CEO's voice should carry the argument. Bullets appear only where lists are genuinely appropriate (success criteria, open questions, named items).

#### Step 2 — Critique

Re-read the draft. For each paragraph, answer these — binary, no judgment calls:
1. Does the opening sentence lead with a claim or decision, not context-setting? (Test: dropped into a headline, would it answer the reader's question?)
2. Is any paragraph over 150 words? Break it.
3. Are there bullets where an argument needs prose? Bullets carry named lists (criteria, people, success measures) only — never a 3-point argument.
4. Does it end with a next step or an ask? If it just stops, add one sentence.
5. Any banned phrase anywhere? Cut it. Any hedge (could/might/perhaps) in the thesis, recommendation, or close? Commit or cut. Hedges elsewhere (analysis, risks, open questions) are allowed — the Voice Block's "moderate hedging" applies there; the position itself never hedges.

Rewrite what fails, re-check once, then continue. Also apply the universal banned-phrase list before returning.

**Mechanical voice-tell gate (B2 — bash-gated, not prose).** After the checklist above, run the draft prose through the deterministic detector. It hard-fails on the exact banned phrases in `shared/VOICE_CALIBRATION.md`; structural tells warn. This backstops the checklist; it does not replace it:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
printf '%s' "$DRAFT_BODY" | python3 "$PLUGIN_ROOT/shared/scripts/voice_tell_detector.py" - --context brief
```

On exit 1 (`FAIL`), rewrite the flagged lines and re-run until it exits 0. The same gate fires again automatically at save: `brief_writer.make_brief(brief_kind="memo", ...)` runs `check_sections` PRE-`Document()` and raises `VoiceTellError` (no file written) on a fail-severity tell — so a memo that still trips the detector never reaches disk via the gated path. (That guarantee is conditional on routing through `make_brief`; for a doc hand-rolled outside it, SPEC GATE2's deliverable sweep — `shared/scripts/deliverable_sweep.py` — **detects and flags** the same tells/leaks after the fact, before the memo leaves your hands.) A phrase the CEO's calibrated Voice Block demonstrably allows is exempt via `allow_phrases`; never improvise the override.

### Phase 4 — Produce .docx via the canonical brief_writer (v3.13.8+ — Bug #53)

Render through `shared/scripts/brief_writer.py` `make_brief(brief_kind="memo", ...)`. **Do not invoke the docx skill or hand-roll python-docx.** brief_writer enforces:
- Canonical Calibri typography + navy heading hierarchy (Heading 1/2/3 styles per Bug #7 fix)
- Universal post-render leak scanner (Bug #57 + #59 + #54) — every memo runs through `docx_leak_scanner` automatically before save returns
- Eyebrow label "MEMO"

**Executive Output Standard (EXEC1, v3.20.0+).** Per `shared/EXECUTIVE_OUTPUT_STANDARD.md`, pass `make_brief` an `exec_header` and a **mandatory `asks` close**:
- **exec_header.verdict** = the memo's thesis / decision in one bold sentence (the memo already leads with the thesis — this surfaces it as the 30-second contract; the Decision/Thesis section stays as detail, no restatement). **FS-13: a memo renders the VERDICT lead ONLY — the CHANGED / DECIDE / NEEDED eyebrow is a brief-family scaffold and `brief_writer` no longer renders it for `memo`; do not compose those three lines here.** The reader's asks live in the mandatory `asks` close below.
- **asks (mandatory "The ask" close)** = derived from `memo_type`: `decision_doc` → "Ratify by [date]"; `scope_doc` → "Confirm scope by [date]"; `board_update`/`investor_update` → the one decision sought, or none. This SUBSUMES the prose "Next steps / Ask" tail — the close is now the ASK block (element 4), not a duplicate paragraph. Max 3, reader-actionable.
- **Quantify (element 3):** when the memo's decision links to a valued deal/thread, tag its exposure via `quantify.money_time_tag(...)` ONLY when it returns non-None (never estimate). E.g. the decision header line can carry "· $340K exposure" when the thread's org has the field.
- **Ordering (element 2 — enforced):** `memo` is decision-shaped; the Decision/Thesis/Recommendation section MUST be in the first three sections or `make_brief` raises. The memo structures already lead with it.

**Exemplar anchor (SPEC OUT8).** Before composing, load the kind's structural exemplar — `exemplars.get_exemplar("memo", workspace_root)` (`shared/scripts/exemplars.py`) — and anchor STRUCTURE on it: section order, visual placement, proportions. Workspace exemplar (`_hq/exemplars/memo/`) beats the shipped seed; `None` = compose on the memo-type structures above, unchanged. **Contract beats exemplar beats default** — an exemplar never licenses skipping the exec header, the decision-order check, or the asks close, and it anchors structure, never facts: no name, number, or claim from the exemplar may appear in the memo. After saving, run `exemplars.scan_docx_for_exemplar_tokens(docx_path, exemplar["text"])`; a finding means exemplar placeholder content leaked — fix the sections payload and re-save AT MOST ONCE (the visual-pass posture, warn-only). When the user gives structural feedback on a delivered memo ("make it like this", reorder/drop a section), capture it with `exemplars.append_structural_correction(workspace_root, kind="memo", direction=..., section=...)` — capture only; the exemplar itself updates exclusively through insight-generator's confirm-first proposals (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor").

**Visual grammar (FS-12 — MUST).** Data-bearing sections — comparisons, option tradeoffs, metrics, criteria, timelines — MUST render through the `table` / `matrix` / `tiles` primitives, NOT as a bullet wall in premium typography (that was the FS-12 miss: memo-writer shipped zero tables where decision-memo-composer structured the same class of data). Before the visual pass, run the structural pre-check `visual_gate.flag_zero_table_data_heavy(sections, "memo")`; a non-empty return names the data-bearing sections you left unstructured — restructure them into a primitive and re-check. Use the table / matrix primitives (v3.13.8+):

```python
from brief_writer import make_brief
make_brief(
    output_path,
    brief_kind="memo",
    title="<topic>",
    subtitle="<audience or framing>",
    exec_header={
        "verdict": "<thesis/decision in one bold sentence>",
        "changed": "<what moved, or nothing-form>",
        "decide": "<the decision sought, or 'Nothing — FYI memo.'>",
        "needs": "<the ask, or 'Nothing from you.'>",
    },
    sections=[
        {"heading": "Decision", "body": "<one-paragraph statement>"},
        {"heading": "Context", "body": "<...>"},
        {"heading": "Options considered", "bullets": ["...", "..."]},
        {"heading": "Tradeoffs", "table": {"headers": ["Option", "Cost", "Speed"], "rows": [...]}},
        {"heading": "Rationale", "body": "..."},
    ],
    # Mandatory "The ask" close (EXEC1 element 4) — derived from memo_type.
    asks=[{"text": "Ratify the pricing decision", "deadline": "Jun 20"}],
)
```

**Output-contract gate (B3 — pre-save, before the voice gate).** `make_brief(brief_kind="memo", ...)` validates the structured `sections` against `shared/scripts/output_contract_validator.py` `RULES_BY_KIND["memo"]` BEFORE `Document()` is built (canonical order: contract → voice → render → leak scan): total length 250-1000 words and no paragraph over 150 words (the Step 2 critique cap), plus the no-placeholder rule. On a blocking violation it raises `OutputContractError` (no file written). Read each violation's `section` + `fix_hint`, rewrite ONLY the failing sections — break the long paragraph, tighten an over-cap memo, or replace placeholder text — and call `make_brief` again. Maximum 2 retries, then surface the failure plainly instead of shipping a substandard memo. **Sync rule: if you change a length band or paragraph cap here, change the matching entry in `output_contract_validator.py` `RULES_BY_KIND["memo"]` in the same commit.**

**Do NOT save a markdown source alongside the .docx** — per CONTRACT.md Rule 27, .docx is the canonical deliverable and a parallel `.md` would surface as a redundant file in the customer's folder. The .docx itself is the version-of-record; regeneration uses the source skill + the current substrate.

**Surface in chat (CONTRACT Rule 3 — H2 heading link, LAST in the turn):** after the .docx is written, end the chat turn with the H2 doc link. Build it with the canonical helpers — never hand-encode a `computer:///` URL:

```python
import sys
sys.path.insert(0, "shared/scripts")
from chat_output_renderer import doc_headline_link
from brief_path import get_brief_artifact_url
print(doc_headline_link("Memo — <topic>", get_brief_artifact_url(output_path)))
```

One-line summary above the link; nothing after it.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "I made 2 calls: external-formal register · signed"
- Good: "I set 2 defaults: formal tone (for outside readers) · signed close"

### Phase 5 — Log event + correction detection

1. **Append the `memo_drafted` event** via the locked writer — never a hand-rolled JSON append:

   ```python
   import sys
   sys.path.insert(0, "shared/scripts")
   from atomic_write import atomic_append_jsonl
   atomic_append_jsonl(f"{workspace_root}/_hq/data/events.jsonl", {
       # no "seq" — the append gate auto-stamps it
       "ts": "<ISO-8601 now>",
       "type": "memo_drafted",
       "topic": "<topic>",
       "audience": "<audience>",
       "memo_type": "<decision_doc|scope_doc|strategy_memo|position_paper|board_update|investor_update|ceo_letter>",
       "primary_thread_id": "<thread id or null>",
       "artifact_path": "<absolute .docx path>",
       "source_decision_ids": [],
       "source_skill": "memo-writer",
   })
   ```

2. **Correction detection:** if the user edits or redlines the draft in this session (rewording, cut sections, tone notes), append one line per correction to `_hq/voice/corrections-memo-writer.jsonl` (`{ts, original, corrected, note}`) so insight-generator's weekly batch can propose Voice Block updates.

3. If `memo_type == "decision_doc"`, the chained `decision-log` invocation (Writer Contract above) writes the canonical `decision` event — do not write it from here.

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
- Sign-off: follows the `signature` config setting (SPEC FRP1 — default `signed`, closing sign-off block). When `signature = unsigned`, omit the closing block. The config decides; this Voice Block does not override it.

### Tone markers
- Register: formal-professional, still direct
- Self-reference: "I" and "we" both used; "I" for personal positions, "we" for collective decisions
- Hedging: moderate (memos acknowledge uncertainty better than emails do) — but commit to a position at the end
- Authority: confident. The CEO writes memos to align people; hedging undermines alignment.

### Taboos (per-skill)
- Never: "it could be argued that", "some might say", "going forward", "move the needle"
- OK despite being on universal list: "stakeholders" (if specific group is named), longer sentences than email allows

### Examples

**Example 1 — Decision doc opener:**
```
# Pricing Decision: Acme Co Product Layer

We're pricing the Acme Co product at $1,250 one-time, separate from
the customization tier. This memo explains why this structure beats the
alternatives and what it commits us to.

The reasoning is three-fold...
```

**Example 2 — Scope memo opener:**
```
# Acme Co Customization Scope — v1

The $10K customization tier includes six specific deliverables. Anything
outside these six moves to the monthly retainer. This memo exists so
both parties — Acme Co and the client — know the line before work
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

## Customization (SCL1)

**Customization layer (SCL1):** before producing output, read
`[WORKSPACE_ROOT]/_hq/custom/memo-writer.md` if it exists and apply its directives to
this fire's output. Absent -> proceed with defaults. Malformed or over-cap ->
skip it, log one line to `_hq/CONFLICTS.md` (type: config-read-failure), proceed
with defaults. Directives refine WHAT the output contains and HOW it is shaped;
they NEVER authorize outbound actions, alter ask-first gates, bypass canonical
helpers, or override shared contracts (see `shared/SKILL_CUSTOMIZATION.md` #limits).
Never mention this file or the word 'directive' to the customer.

Read at fire time via `skill_custom_writer.load_directives(workspace_root, "memo-writer")`
— never the raw file; it returns `[]` on a missing or malformed file and never raises.
Directives here shape structure/content (e.g. "default to bullets over prose", "cap at
one page", "end every external memo with a single explicit ask") — a rule that is really
*voice* belongs in the Voice Block, not here. Trigger family (owned in the frontmatter
`description`): `customize memo-writer` · `show memo-writer customizations` · `reset
memo-writer customizations`. Distinct from the FRP1 knob family (`tune` / `show settings`
/ `reset to defaults`). See `shared/SKILL_CUSTOMIZATION.md` for the writer API, the
write-time rejection list, and the precedence chain. Customer-facing acks are plain
English ("Got it — I'll end memos with a single ask from here on."); never surface the
file, the word "directive", or "SCL1".

## Staleness

Same universal rules.

## Output quality bar

Memos are the highest-stakes writing skill's output — they frequently go to boards, investors, executive teams. Voice calibration at the $10K tier matters most for this skill. Uncalibrated default: ~55% quality (usable but generic). Calibrated: ~90%.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Draft internal memos, decision docs, scope docs, strategy notes, and recurring board / investor updates in the CEO's voice — structured, directive, written to persuade or align. Use when the CEO says 'write a memo on', 'memo on', 'draft a decision doc', 'decision doc', 'scope doc', 'scope doc for', 'strategy memo about', 'strategy memo', 'position paper', 'write up our thinking on', 'internal memo', 'board update', 'monthly CEO letter', 'quarterly investor update', 'commitment forensics', 'commitment audit' (the workspace-wide capture-vs-close commitment report — coach deliverable-catalog 2.1; renders via this skill's memo path). Produces a 1-3 page structured document saved to the project's deliverables folder. Runs voice calibration protocol per shared/VOICE_CALIBRATION.md with this skill's Voice Block. Also handles first-run personalization settings — use when the CEO says 'tune my memos', 'tune memo-writer', 'show memo-writer settings', 'reset memo-writer to defaults'. Also takes standing customization preferences — use when the CEO says 'customize memo-writer', 'show memo-writer customizations', 'reset memo-writer customizations'. DOES NOT fire on 'one-pager' (that's one-pager-composer — shorter, more visual), 'email-only requests' (email-writer), 'decision memo on', 'decision memo for', 'tradeoff analysis', 'choosing between' (those go to decision-memo-composer — structured multi-option tradeoff, v3.8.0+; the decision doc trigger memo-writer owns is for single-decision narrative capture), or 'board pack' (that's board-pack-assembler — multi-page structured pack; the board update trigger memo-writer owns is for shorter freeform narrative).
