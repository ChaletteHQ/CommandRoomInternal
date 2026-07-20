---
name: decision-memo-composer
description: "Walk through a structured tradeoff between options and produce a decision memo .docx — framing, options, weighted criteria, comparison, recommendation. Fires on: 'decision memo on [topic]', 'decision memo for [topic]', 'help me decide between [A] and [B]', 'tradeoff analysis', 'choose between [options]', 'compare [A] vs [B] for [decision]'. Interactive criteria weighting with the CEO, evidence pulled from the workspace where entities are named, one-tap chain to stress-test for the inversion pass, and 'log decision' on the way out. Does NOT fire on 'we decided X' / 'what did we decide' (decision-log — logging/retrieval), 'revisit the [topic] decision' (decision-revisit), 'deal memo on [target]' (single-opportunity evaluation), or 'convene the board' (boardroom). Memo structure and chain points: Routing section in the body."
voice_block_last_refreshed: 2026-05-19
calibration_level: default
template_version: 1.0.0
---

## Deliverable Render Gate (GATE1 — MUST, v3.20.x)

This skill produces a `.docx` deliverable. It MUST be produced through the canonical chokepoint — no exceptions:

- **Render ONLY via `shared/scripts/brief_writer.py` `make_brief(brief_kind="decision_memo", ...)`.** That single call runs the output-contract gate (B3 — required sections, no blank comparison cells), the voice-tell gate (B2), and the post-render leak scan, in that order, BEFORE the file is written.
- **NEVER hand-roll a `.docx`** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship substandard, voice-violating, or PII-leaking documents (the v3.20.0 failure mode).
- **NEVER answer a deliverable request with a chat-only draft.** "Just give me a quick tradeoff" is still a decision-memo request — produce the `.docx` through `make_brief`. Only if the user explicitly says "draft it in chat, don't make a file" do you skip the file — and then say plainly that the gates only run on the rendered file, and offer to produce it.
- **Detectability:** `make_brief` emits a `gate_ran` audit event recording which gates ran. A fire of this skill that yields a document with NO `gate_ran` event for that turn is a flagged bypass (an inferior path was substituted). Pass `workspace_root` to `make_brief` so the event lands in substrate.
- **Visual pass (SPEC OUT2 §3, after every save):** run the render-then-critique pass per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass" — call `shared/scripts/visual_gate.py` `render_preview(<saved path>)`, LOOK at the returned page images against the 7-item checklist (orphaned heading at a page break · empty/placeholder tile · table overflow/wrap damage · cramped spacing · header/footer intact · brand palette applied · chart unreadable / overplotted), fix the sections payload + re-save AT MOST ONCE, then log `visual_gate.log_visual_gate(WORKSPACE_ROOT, doc, rendered, findings, fixed)` either way. `None` from the ladder = no renderer on this machine — log `rendered: false` with a `skipped_reason` and proceed exactly as before (warn-only forever: a finding never refuses a save, and the pass never loops).

If anything below seems to contradict this gate, THIS GATE WINS.

## Skill Boundary (v2.1)

- **Use decision-memo-composer for:** structured tradeoff analysis between 2-4 options. Forward-looking — you have a decision pending and need to think it through.
- **Use `decision-log` for:** CAPTURING a decision you've already made.
- **Use `decision-revisit` for:** revisiting a past decision based on new signal.
- **Use `memo-writer` for:** non-decision memos (scope docs, strategy memos, board updates, position papers).
- **Use `stress-test` for:** mapping how a single existing plan could fail (pre-mortem). Different verb from "choose between options."

## Personification Contract (v3.13.8.4+)

Before composing the .docx decision memo, read `shared/PERSONIFICATION.md` and call `shared/scripts/personification.py::get_brain_name(workspace_root)`. The document header (below the title) uses the shape:

```
Decision Memo · {Title}
Prepared by {brain_name} for {first_name} · {Date}
```

where `{first_name}` comes from `entities.json` `workspace.user_first_name` and `{brain_name}` defaults to `"Penelope"`. No additional name references inside the analysis — the byline carries the personification; the memo content stays formal.

## Writer Contract (v3.8.0+ — substrate-native)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Primary writer for:**
- `[Project]/deliverables/memos/DecisionMemo_[Topic]_[YYYY-MM-DD].docx` — the structured tradeoff memo. Per CONTRACT Rule 27 (no .md deliverables) the output is `.docx`.

**Appends to:**
- `_hq/data/events.jsonl` — event type `decision_memo_drafted` with `{topic, primary_thread_id, options[], weighted_criteria, recommendation, source_decision_ids[], source_intel_ids[], artifact_path}`. Distinct from `memo_drafted` (which memo-writer emits) — this event is structured tradeoff specifically, with the comparison matrix captured.
- **On the `decide [text]` click** → chains to `decision-log` which writes the canonical `decision` event with the memo .docx as rationale link. Mirrors memo-writer's v3.7.1 auto-fire pattern.

**Reads from:** All `events.jsonl` reads come from ONE org-scoped load — **read via the org-scoped reader, never a raw load** (PGUARD2 — the decision memo is a shareable .docx artifact): `from events_io import load_events_org_scoped; org_events, skipped = load_events_org_scoped(workspace_root)`, then filter by `type` at the call site. The reader applies the account-scope mask and drops personal-lane rows by design.
- `_hq/data/entities.json` — project context if a project is referenced.
- `_hq/data/events.jsonl` — `type == "decision"` events on the topic (from the org-scoped load). Surfaces "you already decided X about Y on Z" so the memo doesn't re-litigate settled ground.
- `_hq/data/events.jsonl` — `type == "commitment"` events to surface current load (affects feasibility of "hire now" / "ship now" options) — filter the org-scoped load, or pass it through the seam: `load_open_commitments(events_path, events=org_events)` (PGUARD2 D2 — never the no-arg owner form here).
- `_hq/intel/*.md` — captured intel on the topic (e.g., for a hiring decision, any captured intel on the role / market).
- `_hq/data/entities.json` people-crm records of trusted-advisor people + their stated opinions on the topic from past 1:1 transcripts (via `transcript-search` invocation if needed).
- This skill's Voice Block + `shared/VOICE_CALIBRATION.md`.

**Customer voice-block override (B1):** before drafting, read `_hq/voice/voice-block-decision-memo-composer.md` if it exists — it supersedes the skill's default register (the docs-and-deliverables Voice Block in the shared calibration layer — `shared/VOICE_CALIBRATION.md` + the workspace's calibrated blocks; this file carries no `## Voice Block` section of its own) section-by-section (override sections replace same-named defaults; absent sections fall through). The universal banned-phrase list still applies except where the override's Taboos explicitly carve out an item. Staleness reads the override's `Last refreshed:` first.

**Also reads (SPEC OUT2 §5):**
- `_hq/data/skill_config/decision-memo-composer.json` — first-run knobs, via `skill_config_writer.get_config` (see First-Run Personalization below).
- `_hq/custom/decision-memo-composer.md` — SCL1 standing customization preferences, via `skill_custom_writer.load_directives` (absent → defaults). See the Customization (SCL1) section below.

**Also writes (SPEC OUT2 §5):** `_hq/data/skill_config/decision-memo-composer.json` on first fire, tune, and reset — always via `skill_config_writer` (`save_skill_config` / `wipe_skill_config`), never a raw file write.

**Conflict boundary:** sole writer of `decision_memo_drafted` events. Chained `decision-log` invocation writes the canonical `decision` event (no direct write here).

---

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). Both
decisions are **show-then-tune (STT)**. Read config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22). Bash preamble:
# SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "criteria_persistence": "always_ask",  # always_ask (Phase 2 walks the weights, pre-filled — today) | reuse_last
    "weight_display": "show_math",         # show_math (weighted-score row shown — today) | hide
}
cfg = get_config(workspace_root, "decision-memo-composer", DEFAULTS)
```

- `criteria_persistence = "reuse_last"`: for a decision TYPE (hiring / pricing / vendor / build-vs-buy)
  that already has a prior `decision_memo_drafted` event, reuse that memo's criteria + weights without
  re-asking — the memo states them plainly and the widget's `edit` action remains the escape.
  `"always_ask"` is today's Phase 2 flow (pre-filled, you confirm).
- `weight_display = "hide"`: the Comparison matrix keeps the star scores but drops the weighted-score
  row and the percentage column from Criteria & weights (the math still runs — it's display-only).
  `"show_math"` is today's rendering.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "decision memo on…" | compose with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "decision-memo-composer", DEFAULTS)` BEFORE rendering, then append the first-run footer after the .docx link. |
| **Show settings** | "show decision-memo-composer settings" | render current config in plain English; no memo. |
| **Tune** | "tune decision-memo-composer" / "tune decision memos" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)`. |
| **Reset** | "reset decision-memo-composer to defaults" | `wipe_skill_config(workspace_root, "decision-memo-composer")` → next fire is a first-fire again. |

**The first-run block (footer — after the .docx link):**

> *First time building a decision memo for you. I set 2 defaults: **I'll walk the criteria weights
> with you each time** · **the scoring math stays visible in the memo**. Say **"tune decision
> memos"** to change either, or just tell me ("reuse my criteria for vendor decisions" / "hide the
> math").*

The footer renders exactly once ever (`is_configured` gate).

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "reuse my criteria" / "stop re-asking the weights" | `criteria_persistence = reuse_last` |
| "always ask me the weights" | `criteria_persistence = always_ask` |
| "hide the math" / "just show the stars" | `weight_display = hide` |
| "show the scoring math" | `weight_display = show_math` |

After applying: `save_skill_config(..., is_reconfigure=True)` + confirm in one line.

## Customization (SCL1)

**Customization layer (SCL1):** before producing output, read
`[WORKSPACE_ROOT]/_hq/custom/decision-memo-composer.md` if it exists and apply its directives to
this fire's output. Absent -> proceed with defaults. Malformed or over-cap ->
skip it, log one line to `_hq/CONFLICTS.md` (type: config-read-failure), proceed
with defaults. Directives refine WHAT the output contains and HOW it is shaped;
they NEVER authorize outbound actions, alter ask-first gates, bypass canonical
helpers, or override shared contracts (see `shared/SKILL_CUSTOMIZATION.md` #limits).
Never mention this file or the word 'directive' to the customer.

Read at fire time via `skill_custom_writer.load_directives(workspace_root, "decision-memo-composer")`
— never the raw file; it returns `[]` on a missing or malformed file and never raises. Directives
here are **standing criteria rules** — "vendor decisions always include exit-cost as a criterion",
"hiring decisions always weigh founder time freed at 25%+", "never present more than 3 options
unless I list them myself". A directive adds/pins criteria; it can never override the
evidence-anchor rule, the GATE1 render chokepoint, or the EXEC1 recommendation-first order. Trigger
family (owned in the Routing corpus — the frontmatter description is budget-capped per G11):
`customize decision-memo-composer` · `show decision-memo-composer customizations` ·
`reset decision-memo-composer customizations`. Distinct from the FRP1 knob family (`tune` /
`show settings` / `reset to defaults`). See `shared/SKILL_CUSTOMIZATION.md` for the writer API, the
write-time rejection list, and the precedence chain. Customer-facing acks are plain English ("Got it
— vendor decisions will always weigh exit cost."); never surface the file, the word "directive", or
"SCL1".

---

# decision-memo-composer

The sibling of `memo-writer` with structured tradeoff at the core. Where memo-writer accepts freeform input and produces narrative, this skill FORCES tradeoff thinking — framing, options, weighted criteria, comparison, recommendation. The structure prevents narrative drift around the actual decision.

## What It Does

For a request like "decision memo on whether to hire a Head of Sales now":

1. **Phase 1 — Framing.** Asks scope-clarification questions: what's the deadline, what triggered the decision, what are the candidate options.
2. **Phase 2 — Criteria & weights.** Surfaces a default criteria set tuned to the decision type (hiring, pricing, vendor, scope), asks user to weight them. Pre-fills weights from prior decision-memo drafts if available.
3. **Phase 3 — Substrate pass.** Pulls relevant prior decisions, current commitment load, captured intel, trusted-advisor opinions.
4. **Phase 4 — Draft.** Renders the structured memo. Voice-calibrated per the Voice Block.
5. **Phase 5 — Optional stress-test integration.** If user clicks "Stress-test this," chains to `stress-test` and folds the failure-mode safeguards back into a "What Kills This Decision" section.
6. **Phase 6 — Log decision (optional).** On user click, chains to `decision-log` to write the canonical decision event linking the memo as rationale.

## How to Use

```
"decision memo on whether to hire a Head of Sales now"
"tradeoff analysis between vendor A and vendor B"
"I'm choosing between three pricing models — help me decide"
"weigh Option A vs Option B for the Q3 rollout"
"comparative memo: hire now vs Q4 vs defer"
```

## How It Works

### Phase 1 — Framing

Parse the trigger. Extract decision topic + candidate options if mentioned. If options aren't named in trigger, ask: "What are the candidate options? List 2-4." Also ask:
- "What's the decision deadline?"
- "What triggered this decision now?"
- "Is this tied to a project?" (if so, load project context)

### Phase 2 — Criteria + weights

Surface a default criteria set per decision type. Pre-loaded templates for common types:
- **Hiring:** speed to revenue, founder time freed, hire risk, capital efficiency, org readiness
- **Pricing:** revenue lift, churn risk, sales-cycle impact, perceived value, competitive position
- **Vendor:** total cost, switching cost, time to implement, reliability, integration depth
- **Build vs buy:** time to market, ongoing cost, customization, strategic differentiation

Show defaults, let user adjust the criteria list, then ask: "Weight these by importance (percentages summing to 100)." If user provides "doesn't matter — you pick," default to equal weights.

### Phase 3 — Substrate pass

In parallel:
- Pull `decision` events on the topic from events.jsonl — from the Reads section's org-scoped load (`load_events_org_scoped`), never a raw read. Surface as "Prior decisions on this topic."
- Pull commitment events to assess current load — same org-scoped rows (or `load_open_commitments(events_path, events=org_events)`).
- Pull captured intel on the topic from `_hq/intel/`.
- Identify trusted-advisor people from people-crm (relationship tier 1, role-matched) — search recent meeting transcripts for their stated opinions on the topic.

Inject all of this into the draft prompt as ambient context.

### Phase 4 — Draft via the canonical brief_writer (v3.13.8+ — Bug #53)

Render the .docx through `shared/scripts/brief_writer.py` `make_brief(brief_kind="decision_memo", ...)`. Eyebrow label "DECISION MEMO". Do NOT hand-roll python-docx or use docx-js — brief_writer applies canonical typography, Heading 1/2/3 hierarchy (Bug #7), and runs the universal post-render leak scanner (Bug #57/#59/#54) automatically.

**Executive Output Standard (EXEC1, v3.20.0+) — RECOMMENDATION ABOVE COMPARISON.** Per `shared/EXECUTIVE_OUTPUT_STANDARD.md` element 2, the analysis-first document order (Framing → Options → Criteria → Comparison → Recommendation) is INVERTED at the document level: **the Recommendation section moves ABOVE Comparison** (it now sits right after Framing). Analysis exists so the reader can AUDIT the recommendation, not for suspense. **The interactive Phase-1/2 weight-setting FLOW is UNCHANGED — only the rendered document order flips.** `decision_memo` is decision-shaped, so `make_brief` ENFORCES this: a Recommendation/Decision-headed section first appearing at section index > 2 raises `ValueError`. The `exec_header.verdict` carries the rec + condition + decide-by: *"Option A — hire now, gated on Sales Playbook v0 — decide by Jun 15."*

**Exemplar anchor (SPEC OUT8).** Before composing, load the kind's structural exemplar — `exemplars.get_exemplar("decision_memo", workspace_root)` (`shared/scripts/exemplars.py`) — and anchor STRUCTURE on it: section order, visual placement, proportions (the rec-above-comparison rule above stays enforced regardless). Workspace exemplar (`_hq/exemplars/decision_memo/`) beats the shipped seed; `None` = compose on the structure below, unchanged. **Contract beats exemplar beats default** — an exemplar never licenses skipping the exec header or the ordering check, and it anchors structure, never facts: no name, number, or claim from the exemplar may appear in the memo. After saving, run `exemplars.scan_docx_for_exemplar_tokens(docx_path, exemplar["text"])`; a finding means exemplar placeholder content leaked — fix the sections payload and re-save AT MOST ONCE (the visual-pass posture, warn-only). When the user gives structural feedback on a delivered memo ("make it like this", reorder/drop a section), capture it with `exemplars.append_structural_correction(workspace_root, kind="decision_memo", direction=..., section=...)` — capture only; the exemplar itself updates exclusively through insight-generator's confirm-first proposals (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor").

Use the v3.13.8 `table` + `matrix` section primitives for the criteria + comparison sections rather than synthesizing bullets (this was the Bug #58 precondition):

```python
from brief_writer import make_brief
make_brief(
    output_path,
    brief_kind="decision_memo",
    title="<decision topic>",
    subtitle="<decision-required line>",
    exec_header={
        "verdict": "<picked option + condition + decide-by, one bold sentence>",
        "changed": "<what triggered/shifted, or nothing-form>",
        "decide": "<the choice to make — by <date>>",
        "needs": "<the ask (e.g. 'Approve the hire'), or 'Nothing from you.'>",
    },
    sections=[
        {"heading": "Framing", "body": "<decision required, trigger, deadline, scope>"},
        {"heading": "Recommendation", "body": "<picked option + 1-paragraph why, naming the tiebreaker>"},
        {"heading": "Options", "bullets": ["A. ...", "B. ...", "C. ..."]},
        {"heading": "Criteria & weights",
         "table": {"headers": ["Criterion", "Weight", "Why it matters"], "rows": [...]}},
        {"heading": "Comparison",
         "matrix": {
             "headers_row": ["Option A", "Option B", "Option C"],
             "headers_col": ["Criterion 1", "Criterion 2", "..."],
             "cells": [["★★★", "★★★★", "★★"], ...],
             "star_col_idx": <recommended option's column index>,
         }},
        # "What Kills This Decision" added only if Phase 5 stress-test ran
    ],
)
```

Apply the Universal writing standards in `shared/VOICE_CALIBRATION.md` (structure, specificity, floors — they do not override this skill's Voice Block).

**Mechanical voice-tell gate (B2 — bash-gated, not prose).** Before calling `make_brief`, run the composed memo prose through the deterministic detector. It hard-fails on the exact banned phrases in `shared/VOICE_CALIBRATION.md`; structural tells warn:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
printf '%s' "$DRAFT_BODY" | python3 "$PLUGIN_ROOT/shared/scripts/voice_tell_detector.py" - --context brief
```

On exit 1 (`FAIL`), rewrite the flagged lines and re-run until it exits 0. The same gate fires again at save: `make_brief(brief_kind="decision_memo", ...)` raises `VoiceTellError` PRE-`Document()` (no file written) on a fail-severity tell, so a memo that still trips the detector never reaches disk via the gated path. (That guarantee is conditional on routing through `make_brief`; for a doc hand-rolled outside it, SPEC GATE2's deliverable sweep — `shared/scripts/deliverable_sweep.py` — **detects and flags** the same tells/leaks after the fact, before the memo leaves your hands.) A phrase the CEO's calibrated Voice Block demonstrably allows is exempt via `allow_phrases`; never improvise the override.

**Evidence-anchor every cell.** Each Criterion × Option cell is evidence-anchored, not opinion-anchored: score it, then one sentence citing something concrete (vendor spec, prior logged decision, named constraint). "Option A is faster" fails; "A delivers in 6 weeks per vendor spec vs B's 12 incl. customization" passes.

**The Recommendation MUST name the tiebreaker in the user's specific context** (runway, team size, board pace) — never "best overall choice." The memo is not a comparison; it is a decision with trade-offs explained.

Section structure (EXEC1-ordered — Recommendation above Comparison):
- **Framing** — decision required, trigger, deadline, scope
- **Recommendation** — picked option + 1-paragraph why, naming the tiebreaker. *(EXEC1: moved above Comparison; also surfaced as the `exec_header.verdict`.)*
- **Options** — 2-4 named options with one-line descriptions
- **Criteria & weights** — `table` primitive
- **Comparison** — `matrix` primitive with `star_col_idx` highlighting the recommendation; no blank cells (the audit trail for the recommendation above)
- **What Kills This Decision** — only if stress-test was integrated (Phase 5)

**Output-contract gate (B3 — pre-save, before the voice gate).** `make_brief(brief_kind="decision_memo", ...)` validates the structured `sections` against `shared/scripts/output_contract_validator.py` `RULES_BY_KIND["decision_memo"]` BEFORE `Document()` is built (canonical order: contract → voice → render → leak scan): the required sections (Framing, Options, Criteria & weights, Comparison, Recommendation) must all be present, Options carries 2-4 bullets, and the Comparison `matrix` has NO blank cells (every Criterion × Option cell scored — use "n/a" if a criterion truly doesn't apply, never blank). On a blocking violation it raises `OutputContractError` (no file written). Read each violation's `section` + `fix_hint`, rewrite ONLY the failing sections — add the missing section, right-size the options, or fill the blank cell with a score + evidence — and call `make_brief` again. Maximum 2 retries, then surface the failure plainly instead of shipping a substandard memo. **Sync rule: if you change the required-section list, the options count, or the no-blank-cells rule here, change the matching entry in `output_contract_validator.py` `RULES_BY_KIND["decision_memo"]` in the same commit.**

### Phase 5 — Optional stress-test integration

After the memo renders, offer the chain as ONE plain chat line under the widget: *"Say `stress test this` and I'll pressure-test the recommendation."* (No widget button — `Stress-test this` is not a canonical action, and the typed trigger routes to stress-test directly.) When the user says it, chain to `stress-test` skill with the picked recommendation — stress-test detects the decision-memo context and returns its top 3-5 safeguards as a **structured list** (`{title, failure_mode, safeguard, trigger_date}`, ranked by L×S; see stress-test's "Called from decision-memo-composer" section). Fold those rows directly into the "What Kills This Decision" section — title + failure mode + the safeguard, and the trigger_date when one applies (the compound-drift hard-rethink date).

### Phase 6 — Log decision

The memo .docx is saved and the `decision_memo_drafted` event written when Phase 4 renders — there is no save button; the draft already exists on disk (H2-linked in the widget). Widget actions (all canonical — dispatch in apply-choices' `decision-memo-composer` source entry):

- `decide [text]` (displays "Decide") — chains to `decision-log` to write the canonical `decision` event with the memo as rationale link; non-empty text folds into the rationale.
- `edit [change]` (displays "Edit") — re-runs Phase 4 with the new weights from the input.
- `skip` (displays "Skip") — no write; the memo stays a draft on disk.

## Output Structure (.docx)

**Deliverable link (CONTRACT Rule 3 — H2 heading link, LAST in the turn):** surface the .docx via `chat_output_renderer.doc_headline_link(label, brief_path.get_brief_artifact_url(absolute_path))` as the final line of the chat response — after the widget/summary and Sources, never interspliced mid-body, never a plain-text path, never a hand-built `computer://` URL.

```
DECISION MEMO: Hire Head of Sales — Now vs Q4 vs Defer
Sam Sample | 2026-05-19 | Status: Draft v1

[EXEC HEADER — the 30-second contract, first block]
Option A — hire now, gated on Sales Playbook v0 — decide by Jun 15.
CHANGED   $400K MRR threshold crossed ($478K April); 3 deals stuck >30d.
DECIDE    Timing of the first sales hire — by Jun 15 (board).
NEEDED    Approve the hire (one tap) — or Edit weights.

FRAMING
  Decision required: timing of first dedicated sales hire.
  Trigger: April $42K MRR jump driven by inbound; founder sales
  at capacity. 3 conversations stuck >30d. Deadline: 2026-06-15
  (board).

RECOMMENDATION  (EXEC1 — above Comparison; the verdict's audit trail follows)
  Option A — hire now, with one condition: gate the offer on
  completing the Sales Playbook v0 (~3 days work). Closes the
  Org Readiness gap, shifts A risk grade from ★★ to ★★★, weighted
  score rises to 3.25 — clearly ahead.

OPTIONS
  A. Hire now           (decision in 2w, started Aug 1)
  B. Hire Q4            (decision in 3mo, started Jan 1)
  C. Defer 12 months    (revisit Q2 2027)

CRITERIA & WEIGHTS  (you set these)
  Speed to revenue              30%
  Founder time freed            25%
  Hire risk                     20%
  Capital efficiency            15%
  Org readiness for first SDR   10%

COMPARISON
                       A (now)   B (Q4)    C (defer)
  Speed to revenue      ★★★★      ★★★       ★
  Founder time freed    ★★★★      ★★★       ★
  Hire risk             ★★        ★★★       ★★★★
  Capital efficiency    ★★        ★★★       ★★★★
  Org readiness         ★★        ★★★       n/a
  Weighted score        3.05      2.85      2.10

PRIOR DECISIONS ON THIS TOPIC
  - 2025-11-12: Decided "wait until $400K MRR" — that threshold
    has now been crossed ($478K April).
  - 2026-02-20: Decided "founder-led sales through Q1" — Q1 over.

WHAT KILLS THIS DECISION  (stress-test integration — optional)
  1. Inbound dries up in 60 days; new hire has no warm pipe.
     → Mitigation: 90-day ramp metric tied to OUTBOUND.
  2. Wrong profile (over-indexes enterprise).
     → Mitigation: hire for SMB/mid-market motion explicitly.
```

## DOES NOT

- Make the decision for the user. Surfaces the recommendation; user accepts or rejects.
- Auto-log the decision. Logging requires explicit "Log decision" click.
- Re-litigate a previously-decided topic without surfacing the prior decision. If a `decision` event already exists for this exact topic in the last 90 days, the Phase 3 substrate pass surfaces it prominently as "you already decided this; this memo would supersede" — and the user has to explicitly confirm.
- Capture user-edited weights silently. If user adjusts weights via "Edit weights," the new weights are saved in the `decision_memo_drafted` event for future-self reference.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Walk through a structured tradeoff analysis between options and produce a decision memo .docx with framing / options / weighted criteria / comparison / recommendation. Forward-looking decision support (vs decision-revisit which is backward-looking). Use when the CEO says 'decision memo on', 'decision memo for', 'tradeoff analysis', 'tradeoff analysis for', 'I'm choosing between [A] and [B]', 'I'm deciding between', 'help me decide between', 'weigh [A] vs [B]', 'comparative memo on', 'choose between options for', 'should I [A] or [B]'. Three-pass interactive: (1) framing + ask criteria weights, (2) draft memo, (3) optional stress-test integration. Reads project context, decision-log for prior related decisions, intel-intake for the topic, people-crm for trusted opinions on the topic. Writes decision_memo_drafted event at render; the widget's `decide [text]` action chains to decision-log to write the canonical decision event. DOES NOT fire on 'log decision' (decision-log — capture only), 'what should I decide' (advisory query, route to workspace-manager), 'decision memo about a past decision' (rephrase as decision-revisit), or 'stress test this plan' (stress-test — failure-mode mapping, distinct verb).

> Also handles first-run personalization settings (SPEC OUT2 §5) — use when the CEO says 'tune decision-memo-composer', 'tune decision memos', 'show decision-memo-composer settings', 'reset decision-memo-composer to defaults'. Also takes standing customization preferences — use when the CEO says 'customize decision-memo-composer', 'customize decision-memo', 'customize my decision memos', 'show decision-memo-composer customizations', 'reset decision-memo-composer customizations'. (These verbs live here rather than in the description because the description budget is capped — G11; the runtime router and the trigger tests read the description and this Routing corpus together.)
