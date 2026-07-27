---
name: stress-test
description: "Systematically map every path a plan, decision, or launch could fail — then reverse each failure mode into a structural safeguard. Fires on: 'stress test this plan', 'what could go wrong', 'pre-mortem' / 'pre-mortem on [topic]', 'poke holes in this', 'red team this', 'inversion pass on [plan]'. Munger-style inversion over the plan's own claims plus workspace evidence where entities are named; output is the failure-mode map with safeguards, chat-first with optional .docx. Does NOT fire on 'convene the board' (boardroom — multi-perspective deliberation; this is single-lens failure mapping), 'decision memo' (decision-memo-composer — this chains FROM it as the inversion pass), or 'review this contract' (contract-review). Method and output shape: Routing section in the body."
---

## Skill Boundary (v2.1)

- **Use stress-test for:** pre-mortem analysis. Plan exists → ask how it fails → reverse into safeguards.
- **Use `memo-writer` for:** choosing BETWEEN options (A vs B vs C) — comparative decision memos.
- **Use `cleanup` for:** workspace drift detection (different kind of "what's broken").

## Writer Contract

- **Read-only over the data layer** — with ONE narrow exception (below). No writes to `entities.json`, `events.jsonl` (beyond the writer helper's own config events), `aliases.json`, or `classifier_feedback.jsonl`.
- **Reads from:** the CEO's plan input, relevant project context (if the plan references a project — loads PROJECT_BRAIN.md and session notes), and `_hq/data/skill_config/stress-test.json` via `skill_config_writer.get_config` (SPEC OUT2 §5 — see First-Run Personalization below).
- **Writes (the one exception, SPEC OUT2 §5):** `_hq/data/skill_config/stress-test.json` on first fire, tune, and reset — always via `skill_config_writer` (`save_skill_config` / `wipe_skill_config`; the helper emits the config events), never a raw file write.
- **Produces (file output, not data-layer writes):** a failure-mode-to-safeguard `.docx` saved to `[project]/deliverables/StressTest_[Topic]_[YYYY-MM-DD].docx` (or `_hq/deliverables/` if no project scope).
- **No conflict boundary** — produces a deliverable file plus its own config. Cannot collide with any other skill's data-layer writes.

---

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). Both
decisions are **show-then-tune (STT)** — knobs only; this skill deliberately takes no SCL1
customization layer (the knobs suffice). Read config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22). Bash preamble:
# SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "depth": 5,                    # 5 (all five passes — today) | 3 (Passes 1, 2, 5 only)
    "risk_framing": "aggressive",  # aggressive (blunt kill-risk labels — today) | conservative
}
cfg = get_config(workspace_root, "stress-test", DEFAULTS)
```

- `depth = 3` runs Pass 1 (Pre-Mortem), Pass 2 (Hostile Analyst — still the most important pass),
  and Pass 5 (Inversion Close), skipping the Assumption Audit and Second-Order Map for a faster,
  shorter read. `5` is today's full sequence.
- `risk_framing` sets the LABEL register only — the analysis is identical. `aggressive` is today's
  blunt framing ("The plan dies on the Q3 hire slipping"); `conservative` softens the labels ("The
  plan is most exposed on the Q3 hire") for packs that get forwarded. The verdict names the same
  failure mode either way — framing never dilutes the finding.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "stress test this plan" | run with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "stress-test", DEFAULTS)` BEFORE rendering, then append the first-run footer after the output. |
| **Show settings** | "show stress-test settings" | render current config in plain English; no analysis. |
| **Tune** | "tune stress-test" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)`. |
| **Reset** | "reset stress-test to defaults" | `wipe_skill_config(workspace_root, "stress-test")` → next fire is a first-fire again. |

**The first-run block (footer — after the output):**

> *First time stress-testing a plan for you. I set 2 defaults: **the full five-pass analysis** ·
> **blunt risk labels**. Say **"tune stress-test"** to change either, or just tell me ("keep it to
> the short version" / "soften the language").*

The footer renders exactly once ever (`is_configured` gate).

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "keep it short" / "just the fast version" | `depth = 3` |
| "run the full analysis" | `depth = 5` |
| "soften the language" / "less alarming" | `risk_framing = conservative` |
| "don't pull punches" / "be blunt" | `risk_framing = aggressive` |

After applying: `save_skill_config(..., is_reconfigure=True)` + confirm in one line.

---

# Strategy Stress Test

You run an inversion analysis — mapping every plausible path to failure so the path to success becomes obvious by elimination. This is Charlie Munger's method: "Tell me where I'm going to die, so I'll never go there."

This isn't brainstorming risks. It's systematic failure engineering followed by structural reversal.

## Input

The user gives you a plan, decision, strategy, or goal. Could be detailed or rough — work with whatever they give you.

If the input references one of their projects, load that project's context from the workspace (PROJECT_BRAIN.md, session notes) so the analysis is grounded in real details.

If the input is too vague to analyze ("stress test my business"), ask one question: "What's the specific plan or decision you want me to stress test? The more concrete you can be, the sharper I can be."

## The Five Passes

Run all five in sequence. Each builds on the previous.

### Pass 1: Pre-Mortem

It's 18 months from now. This has completely failed — not stumbled, dead. Write the post-mortem:

- The single decision that sealed it
- The warning sign that appeared early and was rationalized away
- The metric everyone watched that looked fine while the real problem grew underneath
- The day-to-day behavior that seemed reasonable but compounded into catastrophe

Be specific. "Poor execution" isn't a finding. Name the actual decision, the actual metric, the actual behavior.

### Pass 2: Hostile Analyst

You've been hired to destroy this plan. Not dramatically — your job is to engineer the most efficient path to slow, plausible, undetected failure that looks like progress until it's too late. Run three steps, in order — this is a procedure, not a brainstorm.

**2a. Feedback-loop corruption.** For each major decision point in the plan: name the feedback mechanism that would normally catch failure, then name three ways it gets corrupted — (i) signal too small to read, (ii) signal lagged past the response window, (iii) signal suppressed by someone's incentives. For each corruption: "How long until discovery?" — that number is the **silent-failure window**.

**2b. Success-theater diagnosis.** For each milestone: does completion mean the right thing happened, or just that an artifact exists? Describe the **minimum viable fake version** — the cheapest thing that satisfies everyone except the actual user. If you can describe it, the milestone needs a harder definition of done.

**2c. Compound-drift detection.** Name the boring failure: 2-3 metrics each drifting slightly (a number declining 2%/month the plan assumes flat; a timeline gaining 1-2 weeks per checkpoint; a key person's capacity eroding). Identify the **earliest date all of them are simultaneously off-track** — that's the hard-rethink trigger date. Write it into the safeguards.

This is the most important pass. Most people can brainstorm obvious risks. The value here is catching the failures that look like success.

### Pass 3: Assumption Audit

Every assumption baked into the plan. For each:

- **Assumption**: what the plan takes for granted
- **Breaks when**: the specific real-world condition that makes it wrong
- **Likelihood**: high / medium / low
- **Blast radius**: what else fails if this assumption breaks

Don't say assumptions ARE wrong — identify the scenario where they become wrong.

### Pass 4: Second-Order Failure Map

**Layer 1**: The obvious failures everyone already worries about.

**Layer 2**: Failures that only happen because Layer 1 was "handled" — but handled badly. The cascading consequences nobody plans for because they're busy feeling good about addressing Layer 1.

For each Layer 2 failure, trace it back explicitly: "This happens because the response to [Layer 1 problem] was [specific action], which creates [new problem]."

### Pass 5: Inversion Close

Reverse the complete failure map. Every failure mode becomes a structural safeguard — not motivation ("try harder"), not vague advice ("communicate better"). Concrete mechanisms.

- **Failure mode**: from passes 1-4
- **Structural safeguard**: the specific mechanism
- **Implementation**: one sentence on how to put it in place

**Score, don't vibe.** Rate each failure mode `L` (likelihood 1-3) × `S` (severity 1-3), sort by the product descending, take the top 3-5. **Show the L×S score next to each safeguard in the doc** so the CEO can challenge the inputs, not the ranking.

## Called from decision-memo-composer (ADV1 integration)

When stress-test is invoked with a decision-memo context (the user clicked "Stress-test this" in `decision-memo-composer` Phase 5), return the top 3-5 safeguards as a **structured list** — each `{title, failure_mode, safeguard, trigger_date}` (trigger_date from the 2c compound-drift date when one applies, else null) — instead of the freeform doc. The memo folds this into its "What Kills This Decision" section. Keep the same L×S ranking; just hand back structured rows.

## Output Structure

## Deliverable Render Gate (GATE1 — MUST, P1.9)

- **Render ONLY via `make_brief(brief_kind="stress_test", ...)`** — the one call that runs the voice-tell gate and post-render leak scan before the file exists.
- **NEVER hand-roll a `.docx`** (generic docx skill, `python-docx`, docx-js) and **never substitute a chat-only draft** for the file unless the user explicitly asks.
- **NEVER create, render, copy, upload, or update the stress test — or any part, derivative, or restatement of it ("the safeguards", "the failure list", "a summary") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not the project's `deliverables/` folder (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "so the team can own a safeguard each", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the pre-mortem in a Google Doc" is a request this gate refuses, not an override. Hand back the canonical file's link. When stress-test is called from `decision-memo-composer`, the structured safeguard rows go back to that skill — never out through a connector.
- **Detectability:** `make_brief` emits a `gate_ran` audit event — a stress-test doc with no `gate_ran` event that turn is a flagged bypass. Pass `workspace_root`.
- **Visual pass (SPEC OUT2 §3, after every save):** run the render-then-critique pass per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass" — call `shared/scripts/visual_gate.py` `render_preview(<saved path>)`, LOOK at the returned page images against the 7-item checklist (orphaned heading at a page break · empty/placeholder tile · table overflow/wrap damage · cramped spacing · header/footer intact · brand palette applied · chart unreadable / overplotted), fix the sections payload + re-save AT MOST ONCE, then log `visual_gate.log_visual_gate(WORKSPACE_ROOT, doc, rendered, findings, fixed)` either way. `None` from the ladder = no renderer on this machine — log `rendered: false` with a `skipped_reason` and proceed exactly as before (warn-only forever: a finding never refuses a save, and the pass never loops).

**Rendering (v3.13.8+ — Bug #53):** render the `.docx` via `shared/scripts/brief_writer.py` `make_brief(brief_kind="stress_test", ...)`. Eyebrow label "STRESS TEST". Do NOT hand-roll python-docx or use docx-js. brief_writer applies canonical typography, Heading 1/2/3 hierarchy, and runs the universal post-render leak scanner (Bug #57/#59/#54) automatically. Use the v3.13.8 `table` primitive for the safeguard-ranking section.

**Executive Output Standard (SPEC OUT2 §4 — `stress_test` is now a STANDARD_KIND; `make_brief` REFUSES the render without this).** Pass `exec_header`:
- **verdict = the kill-risk line** — the single highest L×S failure mode, named concretely: *"The plan dies on the Q3 hire slipping — everything downstream assumes them onboarded by August."* Never a generic "several risks identified."
- **changed** = only meaningful when re-running against a revised plan (what moved since the last stress test of it), else the nothing-form ("First pass at this plan."). **decide** = the safeguard decision in front of the user (usually: adopt the top safeguard vs accept the risk). **needs** = the one act ("write the hard-rethink trigger date into the plan"), or "Nothing from you."
- **Subsumption (net length must not increase):** the verdict REPLACES any lead sentence of "What to Do (Top Safeguards)" — that section starts directly at the ranked safeguards; the header carries the conclusion.

**Exemplar anchor (SPEC OUT8).** Before composing, load the kind's structural exemplar — `exemplars.get_exemplar("stress_test", workspace_root)` (`shared/scripts/exemplars.py`) — and anchor STRUCTURE on it: section order, visual placement, proportions (safeguards-first stays the rule regardless). Workspace exemplar (`_hq/exemplars/stress_test/`) beats the shipped seed; `None` = compose on the template below, unchanged. **Contract beats exemplar beats default** — an exemplar never licenses skipping the exec header or any gate, and it anchors structure, never facts: no name, number, or claim from the exemplar may appear in the document. After saving, run `exemplars.scan_docx_for_exemplar_tokens(docx_path, exemplar["text"])`; a finding means exemplar placeholder content leaked — fix the sections payload and re-save AT MOST ONCE (the visual-pass posture, warn-only). When the user gives structural feedback on a delivered stress test ("make it like this", reorder/drop a section), capture it with `exemplars.append_structural_correction(workspace_root, kind="stress_test", direction=..., section=...)` — capture only; the exemplar itself updates exclusively through insight-generator's confirm-first proposals (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor").

Lead with the safeguards — that's what the user needs to act on. Supporting analysis below.

```
## Stress Test: [Plan/Decision Name]

[Exec header (OUT2 §4) — the kill-risk line leads:]
**[The single highest L×S failure mode, named concretely.]**
CHANGED   [vs the prior stress test of this plan, or "First pass at this plan."]
DECIDE    [adopt the top safeguard vs accept the risk]
NEEDED    [the one act, or "Nothing from you."]

### What to Do (Top Safeguards)
[The most important guardrails, ranked]

### The Failure Map
#### How it could die — the post-mortem from 18 months out
#### Where a hostile insider would attack
#### Assumptions and where they break
#### Second-order failures (the ones nobody plans for)
#### Every safeguard, ranked
```

## Quality Standards

Every finding must reference specific elements of the actual plan — people, timelines, dependencies, decisions. If you could swap the plan name and the analysis still reads the same, it's too generic.

Have opinions. Some failure modes are much more likely than others. Rank them. Don't hedge everything equally.

---

## What It Doesn't Do

- Does not choose between alternatives — that's `memo-writer` (decision memos compare A vs B vs C).
- Does not run retrospectively on failed projects (post-mortem ≠ pre-mortem). Use a narrative summary instead.
- Does not advocate against the plan — it maps failure paths so the path to success becomes explicit.
- Does not require the plan to be written down formally — works from a rough description or a linked doc.
- Does not replace judgment — the CEO still decides which safeguards are worth the cost.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Systematically map every path a plan, decision, or launch could fail down — then reverse each failure mode into a structural safeguard. Charlie Munger's inversion method. Use when the CEO says 'stress test', 'stress test this', 'stress test this plan', 'pre-mortem', 'pre mortem', 'pre-mortem on', 'what could go wrong', 'red team', 'red team this', 'poke holes', 'poke holes in this', 'poke holes in this launch plan', 'devil's advocate', 'what kills this'. Works on business plans, rollouts, hires, pricing changes, acquisitions, product launches — anything with a defined desired outcome. Produces a failure-mode-to-safeguard .docx. DOES NOT fire on 'what should I decide' (use decision-memo-composer — forward-looking tradeoffs), 'post-mortem on [event]' / 'retrospective on [event]' (out of scope — this skill is pre-mortem only; for re-examining a past DECISION use decision-revisit, and say plainly that event retrospectives have no owner yet), or 'convene the board' / 'what would my board say' (boardroom — multi-seat deliberation; this skill is single-lens failure-mode mapping).

> Also handles first-run personalization settings (SPEC OUT2 §5) — use when the CEO says 'tune stress-test', 'show stress-test settings', 'reset stress-test to defaults'. (These verbs live here rather than in the description because the description budget is capped — G11; the runtime router and the trigger tests read the description and this Routing corpus together.)
