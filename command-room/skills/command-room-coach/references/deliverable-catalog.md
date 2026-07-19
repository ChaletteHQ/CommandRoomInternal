# Deliverable Catalog

**Purpose:** The reference library the coach reads to populate Phase 2C (Outputs). Defines the *shapes* of deliverables coach can offer — not a fixed menu. Each entry resolves at render time to a **specific named deliverable** anchored to a specific entity in THIS workspace.

**Read by:** `command-room-coach` Phase 2C. The coach picks 3-5 entries whose signal-condition fires for the current user + current workspace state, resolves each one's entity slot to a named target, and renders the offer using the entry's render template.

**Hard rule:** Never render an entry whose entity slot can't resolve to something specific in this workspace. A generic offer ("want a strategic memo?") fails the wow bar — drop it instead.

---

## Two delivery patterns

The catalog has two pipes. Coach mixes them by data tier.

| Pattern | Trigger shape | When the wow happens |
|---|---|---|
| **Chained** | `go [entity]` (or `tell me about [person]`) → `[downstream trigger]` | Deliverable depth comes from loading one entity's full history before the produce step. Works at M1 (lazy deep-load fires) and post-onboarding (cached substrate, instant). |
| **Direct** | Single trigger phrase → output | Deliverable depth comes from cross-workspace synthesis across many events. Requires accumulated events.jsonl runtime. Mostly post-onboarding. |

---

## Data tiers

Each entry tags its data tier. Coach reads workspace state to pick eligible entries.

- **`m1_handoff`** — works on (30d light scan + 7d deep scan + onboarding-written files + entities.json seed) alone. Eligible at the M1 first coach fire.
- **`accumulated`** — needs N days of runtime events.jsonl after onboarding (entry specifies threshold). Not eligible at M1 first fire.
- **`either`** — works in both regimes. Renders against M1 substrate at first fire, against richer substrate later.

Selection logic lives in `selection-algorithm.md` — this file is the *what*, that file is the *how*.

---

# Section 1 — Chained deliverables

These are coach's M1-fire bread-and-butter. Each one composes `go` / `tell me about` (substrate-deepening) with a produce-now skill (deliverable-rendering). The `go` step does the work that makes the downstream skill produce something deep.

## 1.1 — Project strategic one-pager

**Data tier:** either
**Delivery pattern:** chained
**Target entity:** project
**Signal condition:** A project with ≥3 mentions in the deep-scan week OR an open strategic tension surfaced in any session note / decision-log entry / meeting transcript (e.g., "is this a flagship or a one-off," "should we widen scope," "is the price right"). At M1, sources are Step 5b deep-dive candidates. Post-onboarding, source is event-density ranking + freshness.
**Trigger chain:** `go [project name]` → `one-pager on [the specific question]`
**Composes with:** `one-pager-composer`
**Synthesis spine:** the project's loaded history (sessions × decisions × meetings × emails) folded into a one-page brief that answers the named strategic question.
**Wow line:** The deliverable names the question the user has been circling without saying it out loud.
**Render template:**
> **The [Project] [angle] one-pager**
>
> *Why now:* [signal — what surfaced in the data, named]
>
> **To produce it — three short steps:**
> 1. Say `go [Project]` to load the project's full context.
> 2. Once you're inside, say `one-pager on [the question]` and I'll produce the brief.
> 3. Say `end session` to close cleanly — that's how the work accrues to [Project]'s history.
>
> *Pattern:* Strategic one-pager — anytime, `go [project]` + `one-pager on [angle]` + `end session`.

**Example (Sam Sample workspace):**
> **The Acme Co flagship-or-outlier one-pager** — Acme is your biggest active client thread (24 mentions across 3 sub-threads in the deep-scan week), with a recurring tension in session notes about whether to invest deeper or keep it as a one-off. Say `go Acme Co` then `one-pager on flagship or outlier` — the brief that names the call you've been circling.

---

## 1.2 — Project strategic memo

**Data tier:** either
**Delivery pattern:** chained
**Target entity:** project
**Signal condition:** A project with substantial loaded history (≥6 events in scan window) AND a strategic angle worth a 1-3 page treatment vs. a one-pager. Use one-pager for sharp questions; memo for narrative / scope / position-paper / strategy.
**Trigger chain:** `go [project name]` → `memo on [angle]` (or `strategy memo on`, `scope doc for`, `position paper on`)
**Composes with:** `memo-writer`
**Synthesis spine:** loaded project history → structured directive memo (framing → analysis → recommendation) in the user's voice via voice calibration.
**Wow line:** It reads like the user wrote it after sitting down for two hours.
**Render template:**
> **The [Project] [angle] memo**
>
> *Why now:* [signal]
>
> **To produce it — three short steps:**
> 1. Say `go [Project]` to load the project's full context.
> 2. Once you're inside, say `memo on [angle]` and I'll produce the memo — 1-3 pages, in your voice, structured to persuade.
> 3. Say `end session` to close cleanly — that's how the work accrues to [Project]'s history.
>
> *Pattern:* Strategic memo — anytime, `go [project]` + `memo on [angle]` + `end session`.

**Example (Sam Sample):**
> **The Northstar Partners "where it goes from here" memo** — your second venture with Bo (April founding); the strategic question that's accumulated across your last 4 Northstar sessions is whether to formalize the revenue side or keep it advisory-shaped. Say `go Northstar Partners` then `memo on the formalize-or-stay-advisory call` — the directive read.

---

## 1.3 — Project pre-mortem

**Data tier:** either
**Delivery pattern:** chained
**Target entity:** project
**Signal condition:** A project with a defined plan / decision / rollout AND no logged stress-test event AND material downside if it fails (size of relationship, capital, time committed).
**Trigger chain:** `go [project name]` → `stress test this plan` (or `pre-mortem on [angle]`, `red team this`)
**Composes with:** `stress-test`
**Synthesis spine:** loaded project plan + history → Munger-inversion failure-mode map + each mode reversed into a structural safeguard.
**Wow line:** The failure modes are project-specific, not generic — they cite the named risk-vectors the project's own history reveals.
**Render template:**
> **The [Project] pre-mortem**
>
> *Why now:* [signal — why this plan deserves the inversion pass now]
>
> **To produce it — three short steps:**
> 1. Say `go [Project]` to load the project's full context.
> 2. Once you're inside, say `stress test this plan` and I'll map every failure path with a structural safeguard for each.
> 3. Say `end session` to close cleanly — that's how the work accrues to [Project]'s history.
>
> *Pattern:* Project pre-mortem — anytime, `go [project]` + `stress test` + `end session`.

---

## 1.4 — Project open-decisions audit

**Data tier:** either
**Delivery pattern:** chained
**Target entity:** project
**Signal condition:** A project with ≥2 decisions raised in scan window that have no `decision_resolved` or `decision_reaffirmed` event. Or any future-self conditional that fired on the project (per coach Phase 2B Insight 4).
**Trigger chain:** `go [project name]` → `review my decisions`
**Composes with:** `decision-revisit`
**Synthesis spine:** every unresolved decision tied to the project + each one's framing-for-closure (the read that would close it) + per-decision action widget (Reaffirm / Supersede / Snooze).
**Wow line:** The user opens it and realizes how many pending decisions on one thread have been quietly aging.
**Render template:**
> **The [Project] open-decisions audit**
>
> *Why now:* [N] decisions raised on [Project] across the scan window, none closed. [Optionally: one specific fired conditional named.]
>
> **To produce it — three short steps:**
> 1. Say `go [Project]` to load the project's full context.
> 2. Once you're inside, say `review my decisions` and I'll surface each pending one with the framing that would close it.
> 3. Say `end session` to close cleanly — that's how the work accrues to [Project]'s history.
>
> *Pattern:* Project decision audit — anytime, `go [project]` + `review my decisions` + `end session`.

---

## 1.5 — Project tradeoff memo (forward-looking decision)

**Data tier:** either
**Delivery pattern:** chained
**Target entity:** project
**Signal condition:** A specific named binary or multi-option decision pending on the project that the user has surfaced in session notes / sessions / meetings without resolving. The decision must be forward-looking (use 1.4 for backward-looking).
**Trigger chain:** `go [project name]` → `decision memo on [specific question]` (or `tradeoff analysis for`, `should I [A] or [B]`)
**Composes with:** `decision-memo-composer`
**Synthesis spine:** loaded project context + decision-log + relevant intel + trusted opinions (people-crm) → framing → options → weighted criteria → comparison → recommendation. Three-pass interactive: framing → draft → optional stress-test integration.
**Wow line:** The memo names the criteria the user weights without having articulated them.
**Render template:**
> **The [Project] [decision] memo**
>
> *Why now:* [signal — the decision specifically named, the recency]
>
> **To produce it — three short steps:**
> 1. Say `go [Project]` to load the project's full context.
> 2. Once you're inside, say `decision memo on [the named question]` and I'll produce the tradeoff: framing, options, weighted criteria, recommended call.
> 3. Say `end session` to close cleanly — that's how the work accrues to [Project]'s history.
>
> *Pattern:* Forward-looking decision memo — anytime, `go [project]` + `decision memo on [question]` + `end session`.

**Example (Sam Sample):**
> **The Acme Co Plugin kill-or-extend memo** — your March 12 session named the conditional "kill auto-update if not shipped by May 1." It's now past that mark, but you haven't sat down with the structured tradeoff. Say `go Acme Co` then `decision memo on kill or extend the plugin auto-update` — the case for each side, weighted, with a recommended call.

---

## 1.6 — Project external-facing briefing one-pager

**Data tier:** either
**Delivery pattern:** chained
**Target entity:** project
**Signal condition:** Project has external stakeholders (board / partner / investor / customer) AND an upcoming externally-visible touchpoint (board meeting, investor update, partner sync, customer QBR) detected on calendar OR mentioned in session notes.
**Trigger chain:** `go [project name]` → `one-pager for the [meeting/audience] on [project]`
**Composes with:** `one-pager-composer`
**Synthesis spine:** loaded project history → audience-tuned brief (different angle vs the internal strategic one-pager in 1.1).
**Wow line:** It's already audience-shaped — the user doesn't have to rewrite it after generation.
**Render template:**
> **The [Project] [audience] briefing**
>
> *Why now:* [signal — the named upcoming touchpoint]
>
> **To produce it — three short steps:**
> 1. Say `go [Project]` to load the project's full context.
> 2. Once you're inside, say `one-pager for [audience] on [Project]` and I'll produce the brief, audience-tuned and ready to send.
> 3. Say `end session` to close cleanly — that's how the work accrues to [Project]'s history.
>
> *Pattern:* External briefing one-pager — anytime, `go [project]` + `one-pager for [audience]` + `end session`.

---

## 1.7 — Person deep dossier

**Data tier:** either
**Delivery pattern:** chained (single-step — people-crm's `tell me about` does both the deepen + the produce)
**Target entity:** person
**Signal condition:** A named person with ≥5 touches in scan window AND no `_people/<slug>/` deep profile yet — OR a person whose existing profile is stale (>60 days since last update).
**Trigger chain:** `tell me about [person]`
**Composes with:** `people-crm`
**Synthesis spine:** every touch with this person (meetings × emails × Slack × intro events × commitment events × decision events) → full relationship dossier (who they are, how you've worked, what's open between you, what's unspoken, your cadence pattern).
**Wow line:** The user reads a dossier on someone they thought they knew well — and the dossier surfaces patterns they hadn't articulated.
**Render template:**
> **The [Person] dossier**
>
> *Why now:* [signal — touch count, recency, deep-profile gap]
>
> **To produce it:** Say `tell me about [Person]` — the full relationship profile lands in the next message.
>
> *Pattern:* Deep person dossier — anytime, `tell me about [person]`.

**Example (Sam Sample):**
> **The Rio Sample 30-day operator dossier** — your most-recent COO partnership (May 13, $22k Month 1), 11 touches in your deep-scan week, no deep profile yet. Say `tell me about Rio Sample` — what's working, where the friction is, what's still unspoken.

---

## 1.8 — Person rescue check-in

**Data tier:** either
**Delivery pattern:** chained
**Target entity:** person
**Signal condition:** Cadence-anomaly insight fired on a named high-stakes person (per coach Phase 2B Insight 3) — 3+ sigma silence relative to their normal cadence on a relationship worth rescuing.
**Trigger chain:** `tell me about [person]` (deepens person memory, primes voice register for that relationship) → `draft a check-in to [person]`
**Composes with:** `people-crm` + `email-writer`
**Synthesis spine:** deepened person memory (last topics, last register, last open thread) → check-in draft in the user's voice, tier-calibrated to that specific relationship.
**Wow line:** The draft references the last live thing the two of them were on, not generic catch-up filler.
**Render template:**
> **The [Person] rescue check-in**
>
> *Why now:* [cadence insight ref — N-day silence vs M-day baseline]
>
> **To produce it — two short steps:**
> 1. Say `tell me about [Person]` to load the relationship context and prime your voice register for that relationship.
> 2. Once loaded, say `draft a check-in to [Person]` and I'll produce a short note in your voice, threaded off the last live thing between you.
>
> *Pattern:* Person rescue check-in — anytime, `tell me about [person]` + `draft a check-in`.

**Example (Sam Sample):**
> **The Bo rescue check-in** — 28-day silence on your Northstar co-founder; prior 60-day cadence was 3-4 days. Say `tell me about Bo Sample` then `draft a check-in to Bo` — short, in your voice, threaded off whatever you two last had open.

---

## 1.9 — Intro draft

**Data tier:** either
**Delivery pattern:** chained
**Target entity:** person (two of them)
**Signal condition:** Two named people in workspace whose recent contexts suggest high mutual relevance (overlapping domain / decision / question) AND no prior `intro_made` event linking them. Detect from cross-mentions in session notes OR meeting transcripts referencing both.
**Trigger chain:** `tell me about [person A]` → `intro [person A] to [person B]`
**Composes with:** `people-crm` + `intro-broker`
**Synthesis spine:** both people's full records + your relationship with each + past `intro_made` events as voice samples → two drafts (double-opt-in + direct-forward), pre-tuned to both sides' context.
**Wow line:** The intro names a specific angle the two of them would actually click on — not a generic "you should know each other."
**Render template:**
> **The [Person A] → [Person B] intro**
>
> *Why now:* [signal — the specific overlap you've noticed]
>
> **To produce it — two short steps:**
> 1. Say `tell me about [A]` to load both relationships into context.
> 2. Once loaded, say `intro [A] to [B]` and I'll produce both drafts (double-opt-in and direct-forward), pre-tuned, ready to send.
>
> *Pattern:* Intro draft — anytime, `tell me about [A]` + `intro [A] to [B]`.

---

## 1.10 — Person 1:1 prep brief

**Data tier:** either
**Delivery pattern:** chained
**Target entity:** person
**Signal condition:** An upcoming meeting on calendar (next 7 days) with a named person who is a high-touch relationship (≥5 touches recent) AND no call-prep event already logged for that meeting.
**Trigger chain:** `tell me about [person]` → `prep for [meeting]`
**Composes with:** `people-crm` + `call-prep`
**Synthesis spine:** deepened person memory → meeting-tuned brief (open commitments owed both ways, last live thread, current state of any shared projects, the question they'll probably bring).
**Wow line:** It surfaces the unspoken question they're likely to bring before they bring it.
**Render template:**
> **The [Person] [meeting/date] prep brief**
>
> *Why now:* [meeting on calendar — date — open thread state]
>
> **To produce it — two short steps:**
> 1. Say `tell me about [Person]` to load the relationship context.
> 2. Once loaded, say `prep for [meeting]` and I'll produce the brief you'd want 5 minutes before.
>
> *Pattern:* 1:1 prep — anytime, `tell me about [person]` + `prep for [meeting]`.

---

## 1.11 — Org rollup brief

**Data tier:** either
**Delivery pattern:** chained
**Target entity:** org (with ≥2 active sub-threads)
**Signal condition:** An org with 2+ active sub-threads in entities.json AND no recent rollup view rendered AND meaningful cross-thread activity (decisions on one thread referencing another, shared people, intertwined commitments).
**Trigger chain:** `go [org] all` → `one-pager on the [org] portfolio`
**Composes with:** `workspace-manager` (rollup view) + `one-pager-composer`
**Synthesis spine:** cross-thread rollup → executive one-pager covering all sub-threads in one read, with the dependencies and cross-cutting risks named.
**Wow line:** The user sees the org as one portfolio for the first time, not three threads they juggle.
**Render template:**
> **The [Org] portfolio one-pager**
>
> *Why now:* [signal — N sub-threads, recent cross-activity]
>
> **To produce it — three short steps:**
> 1. Say `go [Org] all` to load the full cross-thread rollup.
> 2. Once loaded, say `one-pager on the [Org] portfolio` and I'll produce the brief — every sub-thread in one read, with cross-cutting risks named.
> 3. Say `end session` to close cleanly — that's how the work accrues to [Org]'s history.
>
> *Pattern:* Org rollup brief — anytime, `go [org] all` + `one-pager` + `end session`.

**Example (Sam Sample):**
> **The Acme Co portfolio one-pager** — three active sub-threads (Plugin, Business/GTM, Desktop App), cross-activity in recent sessions. Say `go Acme Co all` then `one-pager on the Acme portfolio` — every sub-thread in one read.

---

# Section 2 — Direct deliverables

No precursor. Single trigger fires the deliverable. These synthesize across the whole workspace and need accumulated runtime in events.jsonl to clear the depth bar. Most are `accumulated`-tier, unlocking after the threshold their entry names.

## 2.1 — Commitment forensics report

**Data tier:** accumulated (≥30 days runtime, ≥50 `commitment` events)
**Delivery pattern:** direct
**Target entity:** workspace
**Signal condition:** Commitment-event volume ≥50 AND substrate-integrity insight fires (capture-to-close ratio <30%, per coach Phase 2B Insight 1).
**Trigger chain:** `commitment forensics` (single trigger — produces .docx; routes to `memo-writer`, which claims this trigger)
**Composes with:** `memo-writer` (renders the report via its memo path)
**Synthesis spine:** every commitment event grouped by owed-by-you vs owed-to-you, clustered by project, ranked by relationship-cost, with the close-rate gap analyzed (where you're leaking trust).
**Wow line:** The user sees the specific projects where their close-rate is anomalously low — and the named people who are absorbing that cost.
**Render template:**
> **Your commitment forensics report**
>
> *Why now:* Your capture-vs-close ratio is [X]% on [N] open commitments. Substrate-integrity insight fires.
>
> **To produce it:** Say `commitment forensics` — the report lands in the next message.
>
> *Pattern:* Commitment forensics — anytime, `commitment forensics`.

---

## 2.2 — Decision durability audit

**Data tier:** accumulated (≥90 days runtime, ≥10 `decision` events)
**Delivery pattern:** direct
**Target entity:** workspace
**Signal condition:** Decision-event count ≥10 across last 90 days AND ≥3 of those decisions have subsequent events that either contradict or validate them.
**Trigger chain:** `decision durability audit` (or `review my decisions` at workspace scope)
**Composes with:** `decision-revisit`
**Synthesis spine:** every decision × subsequent events × original named-conditions → three lists (validated, contradicted, conditions-no-longer-hold) with named evidence per row.
**Wow line:** The user sees which decisions the workspace has been quietly validating or contradicting in the background.
**Render template:**
> **Your decision durability audit**
>
> *Why now:* [N] decisions logged in the last 90 days, [M] have subsequent contradicting/validating signal.
>
> **To produce it:** Say `decision durability audit` — the audit lands in the next message.
>
> *Pattern:* Decision durability audit — anytime, `decision durability audit`.

---

## 2.3 — Portfolio velocity scorecard

**Data tier:** accumulated (≥60 days runtime, ≥3 active projects)
**Delivery pattern:** direct
**Target entity:** workspace
**Signal condition:** ≥3 active projects with ≥10 events each across the last 60 days, with a measurable momentum delta vs the prior 60-day window.
**Trigger chain:** `portfolio velocity` (or `which projects are gaining momentum`) — routes to `operator-report`, which claims these triggers
**Composes with:** `operator-report` (renders the scorecard from events.jsonl)
**Synthesis spine:** every active project's 60-day momentum (decisions logged + commitments resolved + session-note freshness + meeting count) vs prior 60 → ranked scorecard: gainers / decayers / "looks active but isn't" anomalies.
**Wow line:** A project the user thinks is active shows up in the decayers column — anchored to the specific gap evidence.
**Render template:**
> **Your portfolio velocity scorecard**
>
> *Why now:* [N] active projects, last 60d vs prior 60d shows momentum deltas worth surfacing.
>
> **To produce it:** Say `portfolio velocity` — the scorecard lands in the next message.
>
> *Pattern:* Portfolio velocity scorecard — anytime, `portfolio velocity`.

---

## 2.4 — Dormant relationship reactivation playbook

**Data tier:** accumulated (≥60 days runtime, ≥5 people with cadence baselines)
**Delivery pattern:** direct
**Target entity:** workspace
**Signal condition:** ≥3 named high-stakes people with cadence-anomaly signal (3+ sigma silence vs their baseline) AND each has historical revenue / strategic-value signal (intel-intake events, commitment volume, project-attachment).
**Trigger chain:** `who went dark` (routes to dormant-customer-scan) OR `dormant playbook` for the multi-person version.
**Composes with:** `dormant-customer-scan`
**Synthesis spine:** ranked list with last-touch date × gap-vs-baseline × historical revenue × inferred why-they-went-quiet × suggested re-engagement angle per person.
**Wow line:** The "why they went quiet" inference for each person draws on specific event-trail signals the user wouldn't have aggregated.
**Render template:**
> **Your dormant relationship reactivation playbook**
>
> *Why now:* [N] high-stakes people 3+ sigma below their baseline cadence.
>
> **To produce it:** Say `who went dark` — the playbook lands in the next message.
>
> *Pattern:* Dormant reactivation playbook — anytime, `who went dark`.

---

## 2.5 — Hidden time-cost report

**Data tier:** accumulated (≥30 days runtime, calendar connector connected)
**Delivery pattern:** direct
**Target entity:** workspace
**Signal condition:** ≥3 recurring meetings (≥4 instances in 30 days) WITHOUT corresponding `meeting_processed` / `followup_pack_drafted` / `memo_drafted` events.
**Trigger chain:** `where am I leaking time` (or `hidden time cost`) — routes to `automation-scanner`, which claims these triggers
**Composes with:** `automation-scanner` (renders the recurring-meeting time-cost analysis)
**Synthesis spine:** recurring-meeting catalog × follow-up-event coverage × estimated extractive cost (frequency × duration × no-output-per-instance).
**Wow line:** A specific weekly meeting the user takes for granted shows up as the biggest time leak — with the hours-per-month named.
**Render template:**
> **Your hidden time-cost report**
>
> *Why now:* [N] recurring meetings without follow-ups in the last 30 days — that's [M] hours of extractive surface.
>
> **To produce it:** Say `where am I leaking time` — the report lands in the next message.
>
> *Pattern:* Hidden time-cost report — anytime, `where am I leaking time`.

---

## 2.6 — Coverage gap memo

**Data tier:** either (works at M1 — coverage gap on day 1 is itself useful)
**Delivery pattern:** direct
**Target entity:** workspace
**Signal condition:** Always eligible (data sparsity is itself a useful signal — coverage gap day 1 = "here's what to fill in"; coverage gap month 3 = "here's what the workspace still doesn't know").
**Trigger chain:** `coverage gaps` (or `what is my workspace missing`)
**Composes with:** `memo-writer`
**Synthesis spine:** active people without deep profiles × active projects without session notes in N days × decisions logged without rationale links × untyped events → ranked memo of what's missing + cost of each gap.
**Wow line:** The user sees what their workspace is *not* seeing — anchored to specific named entities, not categories.
**Render template:**
> **Your workspace coverage gap memo**
>
> *Why now:* [N] highly-active entities without deep memory yet — gaps here cost compounding downstream.
>
> **To produce it:** Say `coverage gaps` — the memo lands in the next message.
>
> *Pattern:* Coverage gap memo — anytime, `coverage gaps`.

---

## 2.7 — Promise-debt audit

**Data tier:** accumulated (≥90 days runtime, ≥5 client kickoffs or commitments logged in kickoff sessions)
**Delivery pattern:** direct
**Target entity:** workspace
**Signal condition:** ≥5 client/project kickoff meetings transcripted ≥30 days ago AND a measurable gap between captured kickoff promises and `commitment_resolved` / `draft_created` / delivery events for each.
**Trigger chain:** `promise debt audit` (or `what have I promised that I haven't shipped`)
**Composes with:** `memo-writer` (memo render)
**Synthesis spine:** transcript-extracted promises per kickoff × delivery-event coverage per promise → table per client: kept / drifted / quietly reframed-without-conversation, with the named drifter and the move that addresses it.
**Wow line:** The "quietly reframed without conversation" column lists specific promises with specific clients — the kind that erode trust gradually.
**Render template:**
> **Your promise-debt audit**
>
> *Why now:* [N] client kickoffs ≥30 days back with measurable promise-vs-delivery gaps.
>
> **To produce it:** Say `promise debt audit` — the audit lands in the next message.
>
> *Pattern:* Promise-debt audit — anytime, `promise debt audit`.

---

## 2.8 — The pipeline snapshot (SPEC PIPE1)

**Data tier:** either (works the moment ≥1 open deal thread exists; richer with accumulated deal events)
**Delivery pattern:** direct
**Target entity:** the open deal set — the offer NAMES at least one specific deal thread (the top-ranked one); zero open deals = entry ineligible (hard rule: never a generic "want a pipeline view?")
**Signal condition:** ≥1 open `kind="deal"` thread. Stronger when a deal carries a rot flag (quiet past its stage window, or no next step) or ≥2 deals share an org — name the strongest signal.
**Trigger chain:** `show my pipeline` (or `pipeline review` for the moved/stuck/closing framing)
**Composes with:** `pipeline-tracker`
**Synthesis spine:** open deal threads × observed activity (meetings/email/commitments on each thread) × stage clocks → ranked report: what's rotting, what's missing a next step, what's closing this month, with dollar tags only where a value was stated.
**Wow line:** The report knows which deal is quietly dying — from the actual contact record, not a field someone forgot to update.
**Render template:**
> **Your pipeline snapshot**
>
> *Why now:* [the named signal — e.g. "[Deal] ([Org]) has been quiet [N] days in [stage]" / "[N] deals are tracked, [M] have no next step"]
>
> **To produce it:** Say `show my pipeline` — ranked, with one-tap moves per deal.
>
> *Pattern:* Pipeline snapshot — anytime, `show my pipeline`; `pipeline review` for the weekly framing.

---

## 2.9 — Un-tuned high-use skill offer (SPEC FRP1)

**Data tier:** accumulated (a skill has accumulated real usage on factory settings)
**Delivery pattern:** direct (a one-line offer, not a produced document)
**Target entity:** a specific skill (e.g. `inbox-triage`)
**Signal condition:** a skill has **>N fires** (count its fire events in events.jsonl — e.g. `pack_run` / `email_drafted` / `meeting_processed` / the skill's own output event — N≈20 as the default high-use bar) AND its ONLY `skill_first_run_configured` event carries `data.origin: first_fire_defaults` with NO later `skill_reconfigured` (i.e. the user has never tuned it). **Fires ONCE EVER per skill** — after the offer is made (record via the `coach_session` event's offered-arcs list), never re-offer the same skill's tune. This is the per-skill nag-guard from SPEC FRP1 D5.
**Trigger chain:** `tune [skill]` (the offer's accept path routes straight to the skill's Tune mode)
**Composes with:** the target skill's first-run Tune mode (per `shared/FIRST_RUN_PROTOCOL.md`)
**Synthesis spine:** per-skill fire-count × config-origin (`first_fire_defaults`-only) → the single highest-use skill the user has never personalized, with its fire count named.
**Wow line:** A skill the user leans on heavily turns out to be running entirely on factory settings — and tuning it is one 60-second step away.
**Render template:**
> **Tune the skill you use most**
>
> *Why now:* You've used [skill] [N] times — all on the factory defaults. A 60-second tune makes it yours.
>
> **To do it:** Say `tune [skill]` — I'll walk the quick setup, or just tell me what you'd change.
>
> *Pattern:* Un-tuned high-use offer — shown once per skill; never repeated.

## 2.10 — What your brain changed this week (SPEC LB1 — the Staff Meeting)

**Data tier:** accumulated (the Living Brain has real activity: ≥1 auto-applied change or resolved/expired proposal in the last 7 days, OR ≥3 open proposals waiting — real counts from the change feed / queue, never invented)
**Delivery pattern:** direct
**Target entity:** workspace
**Signal condition:** `change_feed.changes_since(<7d ago>)` returns ≥1 line, OR `brain_proposals.card_health_counts` shows `open >= 3`. Anchor the render in the actual counts.
**Trigger chain:** `staff meeting` (the full one-tap surface) / `what did you change` (the read-only feed) — both route to `system-health`
**Composes with:** `system-health` (owns the surface); the weekly `staff-meeting` scheduled chat (say `add staff meeting`) is the standing version
**Synthesis spine:** change-feed aggregation (closures from sent mail × sweep recoveries × proposal resolutions/expiries) × the open-queue count → the week's "what ran itself" story with the queue as the ask.
**Wow line:** The system names, with counts, what it handled without being asked — and hands over the exact list of what's waiting on a yes/no.
**Render template:**
> **What your brain changed this week**
>
> *Why now:* [N] things handled on their own this week ([the top feed line, verbatim]), and [M] suggestions are waiting on your yes/no.
>
> **To see it:** Say `staff meeting` — everything reviewable in one sitting, one tap each. Or `what did you change` for the read-only version.
>
> *Pattern:* Weekly review — say `add staff meeting` to make it a standing Monday chat.

---

# Section 3 — Selection notes

(Full algorithm in `selection-algorithm.md`.)

**At M1 first fire:**
- Eligible = entries tagged `m1_handoff` or `either`.
- For chained entries, the entity slot resolves from onboarding's **Step 5b deep-dive candidates** list (already pre-ranked by event density at M1 graduation) — or, if that list isn't readable, from 30-day light-scan entity-frequency rank.
- Direct entries: only `2.6 Coverage gap memo` is eligible at first fire (always works). Other directs suppressed until accumulation thresholds are met.
- Mix target: 3-5 offers, weighted toward 4 chained + 1 direct (coverage gap as the cross-workspace anchor).

**Post-onboarding (any subsequent fire):**
- All `either` and `accumulated` entries become eligible as their thresholds are hit (entry-level — coach checks event counts).
- For chained entries, entity slot resolves from freshness-weighted event-density ranking on events.jsonl (last 30 days).
- Direct entries fire when their accumulation threshold is met.
- Mix target: 3-5 offers, weighted by signal strength. As accumulated tier unlocks, the mix tilts toward direct deliverables for the cross-workspace wow.

**Refresh / re-run mode (`coach_session` event in last 14 days):**
- Coach reads the last `coach_session` event to suppress arcs already offered + un-acted-on (rotate to different entities/patterns to avoid feeling naggy).
- If an offered arc was acted on (downstream event landed in events.jsonl after coach_session.ran_at) → coach opens with one-line acknowledgment and rotates fresh.

**Anti-pattern (drop instead of render):**
- Entity slot can't resolve to a specific named target → drop the entry.
- Signal-condition fires but the named anchor is generic / weak (e.g. "some project" instead of "Acme Co Plugin") → drop the entry.
- Two entries resolve to the same entity → keep the higher-leverage one, drop the other.

---

# Catalog growth

When adding entries, every new entry must declare all required fields (data tier, delivery pattern, target entity, signal condition, trigger chain, composes-with skill, synthesis spine, wow line, render template). Entries without a clear named-render template don't get added — they're too abstract to clear the wow bar.

The catalog is a *what could the coach offer* reference, not a static menu. Coach selects per fire. Adding entries grows the surface; it doesn't change the choreography (Mirror → Insights → 3-5 Outputs → close).
