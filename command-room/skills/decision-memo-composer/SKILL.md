---
name: decision-memo-composer
description: "Walk through a structured tradeoff analysis between options and produce a decision memo .docx with framing / options / weighted criteria / comparison / recommendation. Forward-looking decision support (vs decision-revisit which is backward-looking). Use when the CEO says 'decision memo on', 'decision memo for', 'tradeoff analysis', 'tradeoff analysis for', 'I'm choosing between [A] and [B]', 'I'm deciding between', 'help me decide between', 'weigh [A] vs [B]', 'comparative memo on', 'choose between options for', 'should I [A] or [B]'. Three-pass interactive: (1) framing + ask criteria weights, (2) draft memo, (3) optional stress-test integration. Reads project context, decision-log for prior related decisions, intel-intake for the topic, people-crm for trusted opinions on the topic. Writes decision_memo_drafted event; on 'Log decision' click chains to decision-log to write the canonical decision event. DOES NOT fire on 'log decision' (decision-log — capture only), 'what should I decide' (advisory query, route to workspace-manager), 'decision memo about a past decision' (rephrase as decision-revisit), or 'stress test this plan' (stress-test — failure-mode mapping, distinct verb)."
voice_block_last_refreshed: 2026-05-19
calibration_level: default
template_version: 1.0.0
---

## Skill Boundary

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
- **On "Log decision" click** → chains to `decision-log` which writes the canonical `decision` event with the memo .docx as rationale link. Mirrors memo-writer's v3.7.1 auto-fire pattern.

**Reads from:**
- `_hq/data/entities.json` — project context if a project is referenced.
- `_hq/data/events.jsonl` — `type == "decision"` events on the topic. Surfaces "you already decided X about Y on Z" so the memo doesn't re-litigate settled ground.
- `_hq/data/events.jsonl` — `type == "commitment"` events to surface current load (affects feasibility of "hire now" / "ship now" options).
- `_hq/intel/*.md` — captured intel on the topic (e.g., for a hiring decision, any captured intel on the role / market).
- `_hq/data/entities.json` people-crm records of trusted-advisor people + their stated opinions on the topic from past 1:1 transcripts (via `transcript-search` invocation if needed).
- This skill's Voice Block + `shared/VOICE_CALIBRATION.md`.

**Conflict boundary:** sole writer of `decision_memo_drafted` events. Chained `decision-log` invocation writes the canonical `decision` event (no direct write here).

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
- Pull `decision` events on the topic from events.jsonl. Surface as "Prior decisions on this topic."
- Pull commitment events to assess current load.
- Pull captured intel on the topic from `_hq/intel/`.
- Identify trusted-advisor people from people-crm (relationship tier 1, role-matched) — search recent meeting transcripts for their stated opinions on the topic.

Inject all of this into the draft prompt as ambient context.

### Phase 4 — Draft via the canonical brief_writer (v3.13.8+ — Bug #53)

Render the .docx through `shared/scripts/brief_writer.py` `make_brief(brief_kind="decision_memo", ...)`. Eyebrow label "DECISION MEMO". Do NOT hand-roll python-docx or use docx-js — brief_writer applies canonical typography, Heading 1/2/3 hierarchy (Bug #7), and runs the universal post-render leak scanner (Bug #57/#59/#54) automatically.

Use the v3.13.8 `table` + `matrix` section primitives for the criteria + comparison sections rather than synthesizing bullets (this was the Bug #58 precondition):

```python
from brief_writer import make_brief
make_brief(
    output_path,
    brief_kind="decision_memo",
    title="<decision topic>",
    subtitle="<decision-required line>",
    sections=[
        {"heading": "Framing", "body": "<decision required, trigger, deadline, scope>"},
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
        {"heading": "Recommendation", "body": "<picked option + 1-paragraph why>"},
        # "What Kills This Decision" added only if Phase 5 stress-test ran
    ],
)
```

Section structure:
- **Framing** — decision required, trigger, deadline, scope
- **Options** — 2-4 named options with one-line descriptions
- **Criteria & weights** — `table` primitive
- **Comparison** — `matrix` primitive with `star_col_idx` highlighting the recommendation
- **Recommendation** — picked option + 1-paragraph why
- **What Kills This Decision** — only if stress-test was integrated (Phase 5)

### Phase 5 — Optional stress-test integration

Surface widget action: `Stress-test this`. If clicked, chain to `stress-test` skill with the picked recommendation. Fold its 3-5 failure modes + safeguards into the "What Kills This Decision" section.

### Phase 6 — Log decision

Widget actions: `Save draft`, `Log decision`, `Edit weights`, `Cancel`.

- `Save draft` — saves the .docx, writes `decision_memo_drafted` event.
- `Log decision` — saves the .docx + writes `decision_memo_drafted` + chains to `decision-log` to write the canonical `decision` event with the memo as rationale link.
- `Edit weights` — re-runs Phase 4 with new weights.

## Output Structure (.docx)

```
DECISION MEMO: Hire Head of Sales — Now vs Q4 vs Defer
Sam Sample | 2026-05-19 | Status: Draft v1

FRAMING
  Decision required: timing of first dedicated sales hire.
  Trigger: April $42K MRR jump driven by inbound; founder sales
  at capacity. 3 conversations stuck >30d. Deadline: 2026-06-15
  (board).

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

RECOMMENDATION
  Option A — hire now, with one condition: gate the offer on
  completing the Sales Playbook v0 (~3 days work). Closes the
  Org Readiness gap, shifts A risk grade from ★★ to ★★★, weighted
  score rises to 3.25 — clearly ahead.

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
