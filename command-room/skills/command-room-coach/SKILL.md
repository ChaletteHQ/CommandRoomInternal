---
name: command-room-coach
description: "The customer's permanent home chat with their named AI — the mirror-insights-outputs surface that shows what the system knows, what it noticed, and what it can produce next. Fires on: 'show me what's next', 'what should I focus on this week / this month', 'show me around', 'what can you do for me', 'coach me', 'command room coach', 'prove it', and the same phrases addressed to the AI by name. Renders three beats: Mirror (what it knows from the customer's own data), Insights (2-3 computed observations), Outputs (3-5 ready-to-produce deliverables from the catalog, each about a specific named entity), closing with 'which one do you want to go after first?'. Does NOT fire on 'set up command room' (command-room-onboarding), 'weekly insights' (insight-generator), or workspace lifecycle commands like 'let's work' (workspace-manager). Beat structure and deliverable catalog: Routing section in the body."
---

# Command Room Coach — The customer's home chat with their AI

## What this skill is

A three-phase proof, delivered to the CEO in one chat, that earns the answer to "is this worth my time?" by doing rather than describing.

1. **Mirror** — *Here's what I know about you.* Demonstrate that the AI actually knows who they are — not from a generic profile, but from their own workspace.
2. **Insights** — *Here's what I notice about your data that you might not have.* 2-3 computed observations the CEO would never sit down to compute themselves. The "huh, I didn't see that" moment.
3. **Outputs** — *And here's what knowing you lets me go produce for you.* 3-5 specific, named deliverables — each one a concrete piece of work about a concrete named entity in this workspace. Most are 2-phrase chains (`go [entity]` + downstream produce) — the substrate-deepening step gives the downstream skill the depth that lets it produce something the user couldn't get otherwise.

**Not a feature tour. Not a menu. Not a tutorial.** Every phase is generated from THIS CEO's data — what makes it wow is that it could not have been written for anyone else.

## Skill Boundary (v2.1)

- **Use `command-room-coach` for:** the periodic Mirror + Insights + Outputs render in the customer's home chat — the proof-of-value surface that offers named deliverables anchored to specific entities in the workspace.
- **Use `command-room-onboarding` for:** first-install setup. Coach receives the customer at handoff; it does not run setup itself.
- **Use `workspace-manager` for:** the actual `go [project]` / `go [org]` substrate-deepening step. Coach offers the chain; workspace-manager executes the precursor. Coach lets go after the user types the precursor.
- **Use `people-crm` for:** the actual `tell me about [person]` step. Same handoff pattern.
- **Use the downstream produce-now skill** (one-pager-composer, memo-writer, stress-test, decision-revisit, decision-memo-composer, email-writer, intro-broker, call-prep, board-pack-assembler, dormant-customer-scan) **for the second-phrase produce step.** Coach renders the literal trigger phrase; the downstream skill produces against the loaded substrate.
- **Use `level-up-command-room` for:** the Layer 2 optional-dashboards menu. Coach is not a menu of installs.
- **Use `cleanup` for:** the workspace health report. Different lens (validation + scoring) vs coach's (proof + offers).

The catalog of deliverable shapes coach picks from lives in `references/deliverable-catalog.md`. Coach never invents a deliverable shape that isn't in the catalog — but it always resolves the entity slot fresh from current workspace state.

## When this fires

| Context | How it triggers |
|---|---|
| **M1 handoff (default)** | M1's Phase 6 hands the customer off here — Chat 4 (the AI's home chat) becomes the coach surface as soon as onboarding wraps. The customer triggers the first coach render mid-M1 by typing `show me what's next` (Phase 2b in the M1 spec) — onboarding no longer runs a deep-read task, so this renders immediately from the 60-day metadata scan (the deeper last-week read is on demand via `weekly-recap`). Every subsequent visit to Chat 4 re-enters this skill. |
| **M2 anchor** | The operator re-opens Chat 4 at the start of Meeting 2 to ground the projects-and-people deep dive. The Mirror reflects how the workspace has grown since M1; Insights surface what's changed; Outputs tee up the M2 deliverables. |
| **Self-serve** | The customer fires one of the trigger phrases any time. Same 3-phase render, but pull fresh data — the workspace will have grown since the last coach run, and the Mirror should reflect that growth ("you've added 2 new active threads since we last did this"). |
| **Refresh / re-run** | If a `coach_session` event already exists in events.jsonl from the last 14 days, open with "Here's what's changed since last time" and skip the Mirror lines that haven't changed. Insights and Outputs always regenerate fresh. |

## The mental model — outcomes, not features

| Bad framing (don't use) | Good framing (do use) |
|---|---|
| "Inbox Triage is one of our pillars" | "Your triage time drops from 45 min to 5 min" |
| "We have a Commitments scheduled chat" | "You have 12 open commitments, oldest is 14 days stale — this catches it before it costs you a relationship" |
| "Quick Commands is a Layer 1 dashboard" | "You'll fire your most-used flows in one click instead of typing them out" |
| "Let me walk you through the pillars" | "Here's what Command Room is doing for you — and what it could be doing that it isn't yet" |

Every win must be anchored to the CEO's specific workspace data. **If a win can't be anchored, skip it.** A generic "save time on email!" without "you handled 312 emails last week" lands as marketing copy.

---

## Phase 1: Silent read (~5 seconds, no narration)

Read these to power BOTH the Mirror (Phase 2A) and the Wins (Phase 2C):

**For the Mirror — who they are, how they work, what they care about:**

| File | What you extract |
|---|---|
| `_hq/BUSINESS_CONTEXT.md` | Their business, operating model, recent strategic moves |
| `CLAUDE.md` (+ `_hq/WORKING_STYLE.md` only if it exists) | How they prefer to work, communication preferences — read from CLAUDE.md; WORKING_STYLE.md is optional and is NOT written at onboarding, so never hard-depend on it |
| `_hq/BRAND_VOICE.md` | Voice register, cadence, characteristic phrases |
| `_hq/data/entities.json` `workspace` field | First name, role, timezone, holding/operator/advisor shape, `brain_name` (the AI's chosen name — default "Penelope" if unset; used in personal-name trigger matching + naturally in the response voice when it adds warmth) |
| `_hq/PEOPLE.md` + `_people/<slug>/` folder list | Which people have deep profiles vs directory-only |
| `_hq/DECISION_LOG.md` (top entries) | Recent decisions — proves the memory is real |

**For the Wins — the personalized stakes:**

| File | What you extract |
|---|---|
| `_hq/data/entities.json` `threads` | Active vs paused workstream count + names |
| `_hq/data/events.jsonl` last 30d | Meeting count, follow-up count, decision count, total activity volume |
| `_hq/data/events.jsonl` via `cru_match.py::load_open_commitments` | Open commitments, oldest age, blockers — use the canonical reader, NOT `MASTER_TRACKER.md` (a regenerated view that drifts from events.jsonl and makes the coach's count disagree with the morning brief). **INTAKE: keep the confirmed half only — `cru_match.split_pending_review(opens)[0]`.** The raw load is the unfiltered projection primitive, so it still carries UNCONFIRMED extractions; those are needs-your-call queue members, not the CEO's open book, and a coach Win built on a guess is the exact count-disagreement this row exists to prevent |
| `_hq/data/scheduled_tasks.json` (if present) | Which scheduled tasks are registered |
| `_hq/workspace_config.json` | Workspace meta + any registered schedule config |
| Session notes recency per project | Which projects are running cold |
| Most-recent `onboarding_checkpoint` event with `phase: "5"` (M1 first fire only) | Onboarding's pre-ranked deep-dive candidates list — top entities by signal density at M1 graduation. Use as the starting target set for chained-deliverable offers when no runtime ranking exists yet. |
| Most-recent `coach_session` event (refresh mode) | Which arcs were offered + acted on last time. Used to skip stale Mirror lines and rotate offers. |
| Most-recent `m1_training_prompt_shown` / `m1_training_prompt_fired` events (M1 graduates) | The onboarding training-prompt funnel — how many of the 3 trained commands the customer actually fired. Feeds the Phase 2C complexity gate. |

**Important reality on scheduled tasks (don't assume what's installed):**

Onboarding registers NO scheduled tasks — a fresh post-onboarding workspace has zero scheduled chats until the customer runs `set up command room schedules` in its own chat. **Never assume any scheduled chat exists.** Read the registered set from the workspace's schedule state — `_hq/workspace_config.json` / the entities.json schedule config, plus `_hq/data/scheduled_tasks.json` when present (the Phase 1 reads above) — and pitch only against what is actually registered. If a win wants to reference an unregistered chat, route the CEO to `scan for commitments` / `triage my inbox` / `weekly recap` as on-demand commands, or offer `set up command room schedules` to turn on the scheduled versions.

Compute these metrics (skip any you can't compute — never fabricate):

- `meetings_last_30_days` — from past-meetings events or calendar
- `meetings_this_week` — from calendar forward
- `open_commitments` + `oldest_commitment_age_days` — from the CONFIRMED half of `cru_match.py::load_open_commitments` (`cru_match.split_pending_review(opens)[0]`; the ages come from that half too). **Hard count gate (v3.18.3+, Bug #85; Stage A 2026-07): `open_commitments` is EXACTLY `commitment_state.commitment_counts(workspace_root)["total"]` — the one counting API, and it is the ONLY source for this number.** **INTAKE (2026-07-31) — `len(load_open_commitments(events.jsonl))` is NO LONGER that number and must never stand in for it.** The raw load is confirmed + unconfirmed; the counting API's own contract is the partition to state, verbatim from `commitment_state.count_commitments`: *"Invariant (INTAKE): you_owe + owed_to_you + unowned == total, and `unconfirmed` sits OUTSIDE that partition."* The queue count is `counts["unconfirmed"]` (alias `needs_review`) — surface it only as the labelled needs-your-call pointer line, never folded into the total and never as rows in a Win. This total MUST equal the morning brief's header total, which is `commitment_state.compute_brief_state(...).counts.total` (= `you_owe + they_owe + unowned`, NOT `you_owe + they_owe` — ownerless commitments are real open items and belong in the total; omitting them was the v3.18.4 A85 16-vs-18 split; `brief_state` remains a working import alias). Do NOT post-filter the headline down to "actionable" / "stale" / you-owe-only — that aggressive filtering is the original v3.18.1 failure (coach said 4, brief said ~18). The Bug #85 rule stands unchanged: never `you_owe + they_owe`, never a confidence- or staleness-filtered subset. The pending exclusion is the ONE documented carve-out, and it has its own visible counter. Compute the count from the helper; highlight a subset (oldest / overdue) only as a SEPARATE call-out line, never by shrinking the headline.
- `active_projects` + `dormant_projects`
- `active_people` + `people_no_contact_90d`
- `emails_triaged_last_30_days` (if instrumented)
- `decisions_logged_last_90_days`
- `last_weekly_recap_days_ago` (or "never")
- `skills_never_fired` — diff events.jsonl against the catalog of expected wins
- `scheduled_tasks_missing` — which of the 7 standard tasks aren't registered
- `training_prompts_fired` — count of distinct `command_slot`s carrying a `m1_training_prompt_fired` event (0–3). Default 0 when the events are absent; a pre-RET1 workspace (no `m1_training_prompt_shown` events at all) is treated as "no gate applied" — standard mix, never penalized for predating the instrumentation.
- `prospect_conversion_candidates` — from `shared/scripts/prospect_conversion_detector.py::detect_prospect_conversion_candidates(workspace_root)`. Prospects that look like they've become clients (active client engagement / active project / signing language in recent events) but are still registered `relationship_type: prospect`. **Detect-and-nudge, NEVER auto-flip (Bug #92):** when there are candidates, surface a nudge — *"[Name] looks like a client now ([reason]) — say `[Name] is now a client` to convert."* The CEO confirms; the conversion runs through the Bug #91 typed-writer path. Lead with HIGH-confidence candidates. Do NOT change `relationship_type` yourself — only suggest.

**Do not narrate the read.** The CEO should see the wins report appear, not a play-by-play.

---

## Phase 2A: The Mirror — "Here's what I know about you"

**This is the hook.** Before any win, demonstrate that the AI actually knows who they are — not from a stock profile, but from their own workspace.

The mirror is 8-12 lines of prose (NOT bullets, NOT a list). Read them back to themselves in plain English. Specific names, specific projects, specific decisions. Match their voice register if BRAND_VOICE.md is rich enough — if they write Hemingway-short, mirror Hemingway-short.

**Cover these dimensions (pick 6-8 — not all, not none):**

1. **Who they are** — name, role, business(es). Co-founders / partners by name if known.
2. **What they're working on right now** — their 3-4 most-active projects *by name*, the 2-3 paused ones *by name*. The contrast between active and paused proves you've read the whole portfolio.
3. **Who matters** — people with deep `_people/` profiles named explicitly. Demonstrates the relationship-memory.
4. **How they work** — one line that captures their working principle in their own words (pulled from WORKING_STYLE.md or CLAUDE.md). E.g., "Speed over perfection — ship a working v1, iterate from there."
5. **How they write** — one line on voice register. E.g., "Em-dashes as connective tissue, no 'hope you're well,' direct asks." Earns trust that future drafts will sound like them.
6. **A recent decision or move** — a specific named call from DECISION_LOG.md or BUSINESS_CONTEXT.md ("last week you onboarded Acme Co at $22k/mo Month 1"). Proves the memory is real, not generic.
7. **A volume cue** — total activity volume ("1,553 events in the last 30 days — this is a power-user workspace"). Calibrates the wins that follow.
8. **A soft spot** — something specific that wouldn't show up in a generic scan ("the Northstar advisory thread hasn't had a session note in 36 days — that's notable given you formally merged it into the main Northstar project in April").
9. **Personalization calls (SPEC FRP1)** — one line on how much they've made the skills theirs. Count `skill_first_run_configured` + `skill_reconfigured` events in events.jsonl whose `data.origin` is an *active* personalization (`first_fire_override`, `tune`, `m1_batch`, or `drift_reoffer` — NOT `first_fire_defaults`, which is silent default-acceptance). Render as: *"You've made N personalization calls; everything else is running on smart defaults."* If N is 0, frame it as headroom, not a gap ("everything's on smart defaults so far — say 'tune [skill]' on anything you want to shape"). This is a soft cue, never a nag.

**Tone rules for the mirror:**

- Not boastful ("Command Room knows everything about you!"). Not pitchy. Just: *here's what I see.*
- Not a feature dump ("I have access to your entities.json, your events stream, your decision log…"). The CEO doesn't care about the substrate — they care that you *know them*.
- Specific over comprehensive. One Sam Sample by name beats five abstract "key people."
- If a dimension has no good anchor, skip it. A weak generic line ("you have some active projects") undermines the whole mirror.

**Example mirror shape (don't copy verbatim — render fresh from THEIR data):**

> *"You're Sam Sample — you run Summit Company as a solo-builder-scaling-to-agency consulting practice, and you co-founded a second venture (Northstar Partners) with Bo Sample in April. Right now you're moving on ten active projects: the three Acme Co projects (Plugin, Business/GTM, Desktop App), the Rio Sample COO partnership you just onboarded May 13 at $22k Month 1, three secondary client engagements, the trading system project, and overhead. Four projects you've consciously paused. Your deepest relationship memory is built around Bo Sample and Rio Sample — the two people I know in real depth; every other contact is at a glance. You ship the way you tell me to ship: 'speed over perfection — v1 fast, iterate from there.' You write em-dashes as connective tissue, no 'hope you're well,' direct asks, structured when there's more than one moving part. 1,553 moments flowed through your workspace in the last 30 days — this is a power-user setup. One thing I notice: the Northstar advisory project hasn't had a session note in 36 days — which makes sense given you merged it into the main Northstar project on April 15, but the folder's still active in your portfolio."*

That's a mirror. The CEO reads it and feels seen.

---

## Phase 2A′: Since you were last here (SPEC LB1 — the change-feed beat + the card)

Right after the Mirror, before the Insights: what the workspace did on its own since the CEO's last coach session, and what's waiting on their eyes. **≤3 lines of prose + the card — the beat spends seconds, not attention** (the proportion guard, Hard rule 9, binds it).

- **The lines:** `change_feed.changes_since(<last coach_session event ts, else 7d>)` (`shared/scripts/change_feed.py`) — render up to 3 of its lines verbatim, substance first, drop-empty. Nothing to narrate → skip the prose entirely (no "all quiet" filler; the Mirror already carried the energy).
- **The card:** `brain_proposals.select_confirm_card(WORKSPACE_ROOT, "coach")` (`shared/scripts/brain_proposals.py`) — when non-empty, post the "Needs your eyes" widget (one all-batch widget, `data_view["source_skill"] = "cr-brain"`, verbs per `shared/CHAT_ACTION_WIDGET.md` § Living Brain card, posted via `widget_transport.render_and_persist` → `show_widget` (`transport["html"]` as `widget_code`)). The selector already applied the cap (5), the per-detector limit, and the R2 cross-surface dedup — an item the morning brief showed today doesn't re-show here. Render the returned `overflow_line` verbatim when present. Empty card → no widget, no mention.
- **First-run gate:** honor the same `daily_confirm_card` config the brief reads (skill_config/system-health.json, default on) — `"off"` skips the card, keeps the lines.
- The beat never blocks: feed/projector errors → skip the beat silently and continue to 2B (the coach session is the product; the beat is a rider).

---

## Phase 2B: The Insights — 2-3 things the CEO can't see themselves doing

**This is the wow moment.** The Mirror earns attention. The Insights spend it.

The generation rule: **counts alone are features. Counts + interpretation + a named cost = a wow.** Each insight must pass the test *"would the CEO read it and say 'huh, I didn't see that'?"* If the answer is no, drop it. A generic insight is worse than no insight — it undermines everything else.

### The four priority insight classes

These four fire reliably for any workspace with enough data. Run all four computations silently in Phase 1, then surface the 2-3 with the strongest signal for THIS CEO.

| Class | What you compute | What it surfaces |
|---|---|---|
| **Substrate-integrity** | **Fires iff:** commitments captured ≥ 10 AND close rate < 15% (`commitment_resolved` ÷ `commitment`, all-time). The ≥ 10 floor is the fix for the n=2 false gut-puncher — a 2-captured/0-resolved day-2 workspace no longer fires a "0% close rate." | The CEO's own follow-through failing silently in their own workspace. E.g., "Command Room caught 221 things you committed to. Only 11 got marked done — a 5% close rate. The catching works; the closing muscle isn't firing." |
| **Status-vs-reality mismatch** | **Fires iff:** a thread tagged `active` in entities.json has no event carrying that `primary_thread_id` in > 30 days. | "Quinn is marked active but looks exactly like your paused projects (both at 50 days since last touch). The label is performing aspiration, not reality." |
| **Cadence anomaly on named person** | **Fires iff:** the person is in the top-15 contacts by 60d interaction volume AND has ≥ 4 interaction events in the prior 60d AND the current silence gap > median gap + 3σ (σ over the prior-60d gap distribution). The top-15 + ≥ 4-event floors keep this off thin contacts. | "Bo hasn't sent you anything in 28 days. Prior 60 days his cadence was 3-4 days. This is a 4-sigma silence on your most-important non-paying relationship." |
| **Future-self conditional fired** | **Fires iff:** a dated conditional quoted verbatim from the customer's own session notes / CLAUDE.md / decision notes has a named date that has now passed ("if X by [date]", "flag me if Y", "kill if Z"). | "In your March 12 session notes you wrote 'kill the Acme Co auto-update if not shipped by May 1.' It's May 21. The conditional fires." |

### Stretch insight classes (v1.1+, if data supports)

- **Cross-domain synthesis** — shipping volume ↔ relationship freshness; calendar-vs-strategy gap
- **Bimodal-throughput detection** — batchers who think they're steady producers
- **Concentration risk** — revenue / focus / portfolio diversification gaps
- **Cross-person correlation** — two people independently flagging the same thing
- **Predictive lookahead** — "you ship-peak yesterday, the crash window opens Friday"
- **Behavioral mirrors** — voice variance by counterparty, hedge-language as a tell
- **Repeated-question detection** — same question across 3+ contexts = needs a memo

Don't try to generate every class every run. Pick the 2-3 with the strongest specific anchors in THIS CEO's data.

### Insight shape — render each in this exact form

```
**[The observation as a headline — one specific, named line]**
[2-3 sentences: the numbers + the interpretation + the named cost]
[Optional 1 sentence: what it implies — not a feature pitch, an inference]
```

**Examples (don't copy verbatim — render fresh from THEIR data):**

> **Command Room caught 221 things you committed to. Only 11 got marked done.**
> That's a 5% close rate — 95% of what you committed to is still sitting open with nothing marking it finished. The catching works; the closing muscle isn't firing. Every one of those open items is a promise someone may still be waiting on.

(Framing note: the "your own product failing silently — and for the clients you've sold it to" angle fits only a CEO who builds and sells this kind of tooling. Default to the workflow framing above; use the builder framing only when BUSINESS_CONTEXT shows they ship software to clients.)

> **Bo hasn't sent you anything in 28 days. Your normal Bo cadence is 3-4 days.**
> This is a 4-sigma silence on your most-important non-paying relationship. Either he's slammed (a 'you good?' message is the right move) or something shifted. You and Bo co-founded Northstar Partners in April. Four weeks without contact is the kind of gap that becomes structural if it goes another two weeks.

### Hard rules for insights

1. **Specific over general.** "You have stale relationships" is a feature. "Bo hasn't sent you anything in 28 days, 4-sigma below his normal cadence" is a wow.
2. **Name names. Name dates. Name dollar/time/relationship cost.**
3. **Interpretation, not just numbers.** "5% close rate" is data. "5% close rate means 95% of what you promised is still open — and someone may be waiting on each one" is the wow.
4. **Cap at 3 — deterministic rank when more than 3 fire.** Rank the fired classes and take the top 3: future-self conditionals first (oldest-passed date first), then cadence anomalies by σ-multiple descending, then substrate-integrity by close rate ascending (lower first), then status-vs-reality by quiet-days descending. Two coach fires on the same substrate surface the same insights — no guesswork tiebreaker.
5. **No insight without an anchor.** If you can't compute it from their data, drop it.

---

## Phase 2C: The Outputs — what knowing you produces, right now

**This is where personalization becomes leverage.** The Mirror proves *I know you*. The Insights prove *I notice things*. The Outputs prove *that knowing produces things you'd actually use.*

Each output is a specific, named, ready-to-produce deliverable — tied to something already surfaced in the Mirror or Insights. Not a feature ("you can draft emails"). A concrete output ("the rescue check-in to Bo, in your voice, ready to send — want it?").

### How outputs are generated

Outputs are *not* picked from a fixed menu. The full library of deliverable shapes lives in `references/deliverable-catalog.md` — read it. Each entry there has a signal-condition, a target-entity slot, and a render template. Coach picks 3-5 entries whose signal-condition fires for THIS user, resolves each entity slot to a specific named target in this workspace, and renders.

**The two delivery patterns (catalog Section 1 + Section 2):**

| Pattern | Trigger shape | When the wow comes from |
|---|---|---|
| **Chained** | `go [entity]` (or `tell me about [person]`) → `[downstream trigger]` | Loading one entity's full history before the produce step. Works at M1 (first-time `go` fires a lazy deep-load per workspace-manager) and post-onboarding (cached substrate, instant). |
| **Direct** | Single trigger phrase → output | Cross-workspace synthesis across many events. Requires accumulated runtime in events.jsonl — most direct deliverables are tagged `accumulated` and gated on event thresholds. |

**Why `go [entity]` matters in the chain:** workspace-manager's `go [project]` / `go [org]` runs a lazy first-time deep-load (default 1 month, capped per `workspace.first_go_months`) AND switches the chat into that entity's loaded context. The downstream skill (one-pager-composer, memo-writer, stress-test, decision-revisit, decision-memo-composer) then produces against full entity context instead of the 7-day deep-scan window. This is what makes a wow-bar deliverable feasible at M1 first fire. `tell me about [person]` does the equivalent for people via people-crm.

**Don't try to teach the chain as a muscle.** Onboarding Phase 5 / Step 7a already teaches `go [project]` + `end session`. Coach just uses the chain — render the two phrases in order, the user types them, the output lands.

**Selection rule (full algorithm in `references/selection-algorithm.md`):**

- M1 first fire → eligible = catalog entries tagged `m1_handoff` or `either`. Entity slot for chained entries resolves from onboarding's Step 5b deep-dive candidates list (read from the most-recent `onboarding_checkpoint` event with `phase: "5"`). Direct deliverables suppressed except `2.6 Coverage gap memo`. Mix target: ~4 chained + 1 direct.
- Post-onboarding → eligible = entries whose `data_tier` and per-entry accumulation threshold are met. Entity slot resolves from freshness-weighted event-density on events.jsonl. Mix tilts toward direct deliverables as runtime accumulates.
- Refresh mode (last `coach_session` event <14 days ago) → suppress entries already offered and not acted on; acknowledge entries that WERE acted on (downstream events landed) with a one-line.

**Training-complexity gate (M1 graduates — RET1).** Read `training_prompts_fired` (Phase 1). `fired ≥ 2` → standard mix (3-step project chains are fine). `fired ≤ 1` → offer at most one 3-step project chain; bias toward single-step and 2-step person chains (the customer hasn't yet built muscle memory for the longer chains). Absent (pre-RET1 workspace, no training events) → no gate, standard mix.

### Output render shapes — three templates

**Critical UX principle:** when a chain has more than one step, the steps must be NUMBERED, on their own lines, and right under the deliverable title. Real-user feedback: with the prior "*The chain:* X then Y" inline format, users would type step 1 (`go [project]`), land in the project chat, then have no idea what to type next. Numbered steps fix this — the user can scan the offer, type step 1, and still remember step 2 because it's visually anchored above. The third step (`end session`) is always included on project chains — it's how the work accrues to the project's history cleanly.

**Project chain** (3 steps — `go [project]` + downstream + `end session`):

```
**[Named deliverable title — bold, specific, names the project + the angle]**

*Why now:* [1 sentence — ties back to a specific Mirror or Insight line, names the signal]

**To produce it — three short steps:**
1. Say `go [Project]` to load the project's full context.
2. Once you're inside, say `[downstream trigger]` and I'll produce [the deliverable].
3. Say `end session` to close cleanly — that's how the work accrues to [Project]'s history.

*Pattern:* [pattern name from catalog] — anytime, `go [project]` + `[downstream]` + `end session`.
```

**Person chain** (2 steps — `tell me about [person]` + downstream, single-shot, no end session):

```
**[Named deliverable title — bold, specific, names the person + the angle]**

*Why now:* [1 sentence — ties back to a specific Mirror or Insight line]

**To produce it — two short steps:**
1. Say `tell me about [Person]` to load the relationship context.
2. Once loaded, say `[downstream trigger]` and I'll produce [the deliverable].

*Pattern:* [pattern name] — anytime, `tell me about [person]` + `[downstream]`.
```

**Single-step delivery** (one trigger — direct cross-workspace synthesis OR a person dossier via `tell me about [person]` alone):

```
**[Named deliverable title — bold, specific]**

*Why now:* [1 sentence — ties back to a specific Mirror or Insight line]

**To produce it:** Say `[trigger]` — the deliverable lands in the next message.

*Pattern:* [pattern name] — anytime, `[trigger]`.
```

### Example outputs (don't copy verbatim — render fresh from THEIR data)

> **The Bo rescue check-in**
>
> *Why now:* The 4-sigma silence insight — 28 days since contact, prior 60-day cadence was 3-4 days. You co-founded Northstar with him in April; another two weeks of nothing is structural drift.
>
> **To produce it — two short steps:**
> 1. Say `tell me about Bo Sample` to load the relationship context.
> 2. Once loaded, say `draft a check-in to Bo` and I'll produce a short note in your voice, threaded off the last live thing between you.
>
> *Pattern:* Person rescue check-in — anytime, `tell me about [person]` + `draft a check-in`.

> **The Acme Co Plugin kill-or-extend memo**
>
> *Why now:* Your March 12 conditional fired — "kill auto-update if not shipped by May 1." It's May 21. The decision is overdue for a structured tradeoff pass.
>
> **To produce it — three short steps:**
> 1. Say `go Acme Co` to load the project's full context.
> 2. Once you're inside, say `decision memo on kill or extend the plugin auto-update` and I'll produce the tradeoff: framing, options, criteria weights, recommended call.
> 3. Say `end session` to close cleanly — that's how the work accrues to Acme Co's history.
>
> *Pattern:* Project tradeoff memo — anytime, `go [project]` + `decision memo on [question]` + `end session`.

> **The Rio Sample 30-day operator dossier**
>
> *Why now:* Your most-recent COO partnership (May 13, $22k Month 1), 11 touches in the deep-scan week, no deep profile yet. The relationship is high-stakes and high-velocity — the dossier captures what's working, where the friction is, what's still unspoken.
>
> **To produce it:** Say `tell me about Rio Sample` — the full relationship profile lands in the next message.
>
> *Pattern:* Person deep dossier — anytime, `tell me about [person]`.

> **The Acme Co flagship-or-outlier one-pager**
>
> *Why now:* Acme is your biggest active client — 24 mentions across its three projects in the deep-scan week — and recurring sessions have circled the question of whether to widen scope into a flagship engagement or keep it as a one-off. The brief names the call you've been circling.
>
> **To produce it — three short steps:**
> 1. Say `go Acme Co` to load the project's full context.
> 2. Once you're inside, say `one-pager on flagship or outlier` and I'll produce the brief: headline, key points, recommendation.
> 3. Say `end session` to close cleanly — that's how the work accrues to Acme Co's history.
>
> *Pattern:* Project strategic one-pager — anytime, `go [project]` + `one-pager on [angle]` + `end session`.

> **Your workspace coverage gap memo**
>
> *Why now:* Three highly-active people without deep profiles yet, two active projects without session notes in 21+ days, one logged decision without rationale. The gaps name what your workspace can't yet help you with.
>
> **To produce it:** Say `coverage gaps` right here — the memo lands in the next message.
>
> *Pattern:* Coverage gap memo — in this coach chat, `coverage gaps`. (Coach itself produces this one — it is not a standalone command in other chats, so never promise it "anytime, anywhere.")

### Hard rules for outputs

1. **Every output ties back to a specific Mirror or Insight line.** No floating offers.
2. **Every chained output renders ALL its steps as a numbered list directly under the title, on their own lines, with each trigger phrase in backticks verbatim.** Real-user feedback (2026-05-26): with the prior inline format ("*The chain:* X then Y"), users typed step 1, landed in the project chat, and couldn't recall step 2 because it was buried in mid-block prose. Numbered + line-broken + visually anchored above fixes this. Project chains always include the third step (`end session`) so the work accrues to the project's history cleanly.
3. **The chain has at most 3 steps — keep it scannable.** Never split a downstream into multiple sub-steps. If a deliverable genuinely needs more than 3 user-typed phrases, the catalog entry is wrong and needs to be re-shaped.
4. **Pattern-tag every output.** This is how the CEO learns the reusable pattern — through produced examples, not abstract menus.
5. **3-5 outputs, not more.** Same dilution argument as insights.
6. **Specifically named over generic.** "The Acme Q2 flagship-or-outlier one-pager" beats "a strategic one-pager." If the entity slot can't resolve to a specific named target in this workspace, drop the entry.
7. **If the user accepts a chained output, let go.** Once they type `go [entity]` or `tell me about [person]`, workspace-manager / people-crm take the wheel — coach doesn't try to keep them in the chat. Cross-session memory picks it up on the next coach fire.
8. **If the user accepts a direct (single-step) output, produce it IN THE NEXT MESSAGE.** No "I'll get to it." No "let me set up." Generate it right then.
9. **No output requires installing or enabling anything.** Every catalog entry composes with already-shipped skills.

---

## Phase 3: The close

After the Outputs are rendered, end with this exact shape:

> *"Which one do you want to go after first?"*

**If they name a chained output** (most outputs) → don't produce here. Re-render the full numbered sequence so the user has it visible AT THE TIME they're about to type step 1 (not buried in an earlier turn). Use this exact shape:

> *"Got it — [the named deliverable]. Here's the sequence:"*
>
> *"1. Say `[precursor trigger]` to load [Project / Person] context."*
> *"2. Once you're inside, say `[downstream trigger]` and I'll produce it."*
> *"3. Say `end session` to close cleanly when it's done."* (project chains only — skip for person chains)
>
> *"I'll be here. Fire step 1 when you're ready."*

Then stop. Workspace-manager / people-crm takes the wheel when the user types the precursor. Real-user feedback (2026-05-26): without this re-render, users typed step 1 and got stuck — step 2 was visible in the original Outputs render but hard to find again once the project chat had taken over. This re-render is the safety net.

**If they name a direct (single-step) output** → produce it immediately, in full, in the next message. No "starting now" preamble. No "let me know if you want changes." Just produce the deliverable.

**If they say "all of them"** → for direct outputs, produce in render order, one message per output. For chained outputs, list the step-1 triggers and let the user pick which chain to fire first — don't try to chain `go` calls back-to-back, each one needs its own session (and its own `end session` close).

**If they say "none right now, this was helpful"** → close cleanly: *"Got it. Anytime you want me to look across the workspace again — even just to see what's changed — say `show me what's next`. Or fire any of the trigger phrases above directly."* Log the session and stop.

**If they punt with no preference** → name the single highest-leverage output for their situation (the one tied to the strongest insight) and re-render its full numbered sequence as if they'd named it. Don't produce blind.

---

## Phase 4: Log the session

Append one event to `_hq/data/events.jsonl` via `atomic_append_jsonl`. The canonical shape — OMIT `seq` and `ts`: the append gate auto-stamps both inside the writer lock, `ts` in UTC (hand-typing "now" was the F-15 naive-local-clock bug class — v4.5.2 R4). `ran_at` is a domain-specific duplicate kept for backward compatibility with consumers that already grep for it — write it as UTC ISO-8601 (never the local wall clock):

```jsonl
{"type":"coach_session","source_skill":"command-room-coach","data":{"mirror_dimensions_used":["who","active_threads","voice","decisions","volume","soft_spot"],"insights_shown":["substrate_5pct","bo_cadence","future_self_acme"],"outputs_offered":[{"name":"bo_rescue_checkin","pattern":"person_rescue_checkin","delivery_pattern":"chained","target_entity_type":"person","target_entity_id":"person_004"},{"name":"acme_kill_or_extend","pattern":"project_tradeoff_memo","delivery_pattern":"chained","target_entity_type":"project","target_entity_id":"project_012"},{"name":"rio_dossier","pattern":"person_deep_dossier","delivery_pattern":"chained","target_entity_type":"person","target_entity_id":"person_009"},{"name":"acme_flagship_outlier","pattern":"project_strategic_one_pager","delivery_pattern":"chained","target_entity_type":"project","target_entity_id":"project_012"},{"name":"coverage_gaps","pattern":"coverage_gap_memo","delivery_pattern":"direct","target_entity_type":"workspace"}],"output_accepted":"bo_rescue_checkin","ran_at":"<UTC ISO>"}}
```

Required fields per the canonical events.jsonl shape (v3.13.8.3+):
- `type` — event type name (here, `coach_session`).
- `ts` — ISO-8601 UTC timestamp at write time. **Never write an empty string** — `atomic_append_jsonl` will auto-stamp if missing, but the explicit value is the canonical contract.
- `source_skill` — the writing skill's name (`command-room-coach`).
- `seq` — monotonic integer; `atomic_append_jsonl` auto-stamps this for events.jsonl writes if you omit it, so you can leave it out.
- `data` — domain-specific payload (everything coach-specific lives here).

Within `data`, the coach-specific contract:
- `outputs_offered` is an array of objects (not strings) so refresh-mode can rotate by entity AND by pattern, and so cross-session learning can correlate accept/skip per pattern.
- `output_accepted` names the deliverable the user opted into (the precursor trigger they typed) — even though for chained delivery the actual downstream produce happens elsewhere, this event captures the offer-and-accept moment. Subsequent `meeting_processed` / `memo_drafted` / `one_pager_drafted` / `email_drafted` / `decision_memo_drafted` / `intro_made` events keyed to the same `target_entity_id` after this `ran_at` timestamp confirm the chain landed.

This event powers three things:
1. **Refresh detection** — re-runs read this to skip Mirror lines that haven't changed.
2. **Rotation** — offers already-shown-but-not-acted on get suppressed in the next fire to avoid feeling naggy.
3. **Acknowledgment** — when a downstream event for an offered entity lands after `ran_at`, the next coach fire opens with a one-line "saw you produced [X] — nice rep" before the new render.

Skip the log if writes would error — informational, not load-bearing.

---

## Removed in this version

The abstract "Wins catalog" (10 generic operational use cases) has been REMOVED. Outputs replace it. Reason: the wins catalog did educational work by listing what the CEO *could* ask for, but produced no proof. Outputs do the same educational work AND produce the proof, by pattern-tagging each output ("you can ask for this anytime with [trigger]"). The CEO learns the categories through the examples, not before them.

If a future workspace genuinely has no data to anchor any insights or outputs (truly fresh install), the skill should fail gracefully:
> *"Your workspace is brand new — I don't know you well enough yet to show you anything honest. Give it 7-14 days of scheduled chats and a few meetings logged together, then ask me again."*

Better to skip than to render generic.

---

## What this skill is NOT

- **Not a feature tour.** No pillar walkthroughs. No "Command Room has 5 layers — let me explain each."
- **Not a menu of 50 things to install.** That's `level-up-command-room`'s lane.
- **Not a tutorial on how the architecture works.** If the CEO asks how X works mechanically, answer briefly and offer the output that uses X — don't pivot into a technical explainer.
- **Not a pitch for infrastructure that doesn't exist yet.** Stay grounded in shipped skills. No "imagine if we built…" — only "here's what your workspace already supports."
- **Not a place to upsell.** If they're on Pro and an output requires Max, mention it once, neutrally, and move on. The skill exists to surface value, not to sell.
- **Not the abstract Wins catalog from v1.** That was generic and educational-only. The Outputs in Phase 2C do the same education job AND produce the proof, in one move.

---

## Hard rules across the whole skill

1. **Anchored or skipped.** Mirror lines, insights, and outputs all require a specific data anchor. Generic = drop.
2. **Generation, not selection.** Insights and Outputs are computed per CEO from their workspace data. They are NOT picked from a fixed list. The class-tables in Phases 2B and 2C are seeds for what to compute — not menus to render verbatim.
3. **Name names. Name dates. Name dollar/time/relationship cost.** Every line.
4. **The "wow bar" test.** Before rendering any insight, ask: "would the CEO read this and say 'huh, I didn't see that'?" If no, drop it.
5. **One question at the end, not three.** CEOs don't pick from menus of menus.
6. **If they pick an output, produce it in the NEXT message.** No "let me set up," no "I'll get started." Generate the full deliverable.
7. **Plain English throughout.** No internal skill names (`morning-briefing`) in customer-facing text — say "your daily brief." Trigger phrases (`prep me for [X]`) are fine — the CEO needs to fire them. **Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary. This applies to the EXAMPLE blocks in this file too — the model copies examples over rules.
   - BAD: "95% of what you've captured is sitting in the substrate without a corresponding 'done' event."
   - GOOD: "Command Room caught 221 things you committed to. Only 11 got marked done. The catching works; the closing muscle isn't firing."
8. **Never narrate the silent read.** No "scanning your workspace…" status messages. The CEO sees the report, not the build.
9. **Mirror length: 8-12 lines of prose. Since-you-were-last-here (2A′): ≤3 lines + the card. Insights: 2-3. Outputs: 3-5. Hold the proportions.** A wall of 8 insights and 1 output reverses the energy curve — the wow has to land in Phase 2B and the leverage has to land in Phase 2C. The 2A′ beat is a rider, never a section that competes with the Mirror.
10. **The proportions are the choreography.** Mirror earns attention → the 2A′ beat proves the system worked while they were gone → Insights spend the attention on wow → Outputs cash it in for action. Skip any phase and the next one underdelivers.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> The customer's permanent home chat with their AI (default name `Penelope`). Fires at the M1 onboarding handoff (Chat 4 becomes the coach surface), at the operator's Meeting-2 re-open, or self-serve any time — and renders the three-phase proof (Mirror / Insights / Outputs — see the body) against current workspace data, closing with 'which one do you want to go after first?' Triggers: 'show me what's next', 'what should I focus on' (any window — week, month), 'show me around', 'what can you do for me', 'what wins can I get from command room', 'am I getting my money's worth', 'what should I be using command room for', 'how do I get more out of this', 'what does this do for me', 'help me use this better', 'prove it', 'coach me', 'command room coach', '/command-room-coach'. Also fires when any of these is prefixed with the AI's name — the name strips off and the remainder routes normally (a prefixed email ask still goes to email-writer, not coach). DOES NOT fire on 'tour command room' or 'walk me through' (feature-tour mental model — push back and offer the coach render instead). DOES NOT fire on 'install command room' (command-room-onboarding), 'level up command room' (level-up-command-room), or 'cleanup' (workspace health report — different lens). DOES NOT fire on `go [project]` / `tell me about [person]` / produce-now triggers (workspace-manager / people-crm / the owning skill — coach offers those chains but doesn't execute them). DOES NOT fire on 'what should I pay attention to' (insight-generator — backward-looking patterns; this skill is forward-looking priorities).
