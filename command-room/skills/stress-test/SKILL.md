---
name: stress-test
description: "Systematically map every path a plan, decision, or launch could fail — then reverse each failure mode into a structural safeguard. Fires on: 'stress test this plan', 'what could go wrong', 'pre-mortem' / 'pre-mortem on [topic]', 'poke holes in this', 'red team this', 'inversion pass on [plan]'. Munger-style inversion over the plan's own claims plus workspace evidence where entities are named; output is the failure-mode map with safeguards, chat-first with optional .docx. Does NOT fire on 'convene the board' (boardroom — multi-perspective deliberation; this is single-lens failure mapping), 'decision memo' (decision-memo-composer — this chains FROM it as the inversion pass), or 'review this contract' (contract-review). Method and output shape: Routing section in the body."
---

## Skill Boundary (v2.1)

- **Use stress-test for:** pre-mortem analysis. Plan exists → ask how it fails → reverse into safeguards.
- **Use `memo-writer` for:** choosing BETWEEN options (A vs B vs C) — comparative decision memos.
- **Use `cleanup` for:** workspace drift detection (different kind of "what's broken").

## Writer Contract

- **Read-only over the data layer.** No writes to `entities.json`, `events.jsonl`, `aliases.json`, or `classifier_feedback.jsonl`.
- **Reads from:** the CEO's plan input, relevant project context (if the plan references a project — loads PROJECT_BRAIN.md and session notes).
- **Produces (file output, not data-layer writes):** a failure-mode-to-safeguard `.docx` saved to `[project]/deliverables/StressTest_[Topic]_[YYYY-MM-DD].docx` (or `_hq/deliverables/` if no project scope).
- **No conflict boundary** — produces a deliverable file only. Cannot collide with any other skill's data-layer writes.

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
- **Detectability:** `make_brief` emits a `gate_ran` audit event — a stress-test doc with no `gate_ran` event that turn is a flagged bypass. Pass `workspace_root`.

**Rendering (v3.13.8+ — Bug #53):** render the `.docx` via `shared/scripts/brief_writer.py` `make_brief(brief_kind="stress_test", ...)`. Eyebrow label "STRESS TEST". Do NOT hand-roll python-docx or use docx-js. brief_writer applies canonical typography, Heading 1/2/3 hierarchy, and runs the universal post-render leak scanner (Bug #57/#59/#54) automatically. Use the v3.13.8 `table` primitive for the safeguard-ranking section.

Lead with the safeguards — that's what the user needs to act on. Supporting analysis below.

```
## Stress Test: [Plan/Decision Name]

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
