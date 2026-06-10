---
name: insight-generator
description: "Weekly synthesis pass that surfaces patterns the CEO wouldn't have seen. Fires on: 'weekly insights', 'what am I missing', 'run insights', 'synthesize the week', 'what should I pay attention to', 'cross-project patterns', 'what's drifting', 'show me the insight report', 'generate insights', 'classification review', 'review classifications', 'review my projects', 'review project proposals', 'new project proposals'. Also runs automatically on a weekly schedule (Sunday evening by default) via scheduled-tasks. Reads generated views (TIMELINE, RELATIONSHIPS, COMMITMENT_AGING, DORMANT, THEMES) + entities.json + events.jsonl and produces (a) a ranked list of observations worth the CEO's attention, (b) a batched classification review pass covering every provisional / low-confidence event from the prior week (Pass 8), (c) a batched project-proposal pass surfacing new projects when accumulated signal suggests outcomes will slip without a dedicated anchor (Pass 9), and (d) a batched org-proposal pass surfacing new orgs when capture events accumulate enough signal without an explicit attribution (Pass 10). Saves the synthesis to _hq/insights/[YYYY-MM-DD]_insights.docx (v3.13.0+) and surfaces a link at the bottom of the chat turn. Pass 8 appends `classification_review` + `reclassification` events and `classifier_feedback.jsonl` rows. Pass 9 appends `project_proposal` events + `classifier_feedback.jsonl` rows and delegates entities.json mutations to workspace-manager. Pass 10 appends `org_proposal` events + `classifier_feedback.jsonl` rows of `type: org_proposal`. Does NOT handle one-off questions — use workspace-manager for that."
---

# Insight Generator — Command Room (v2.1)

The differentiation layer. Morning briefings tell the CEO what happened. This skill tells them what the data means that they wouldn't otherwise notice.

## Writer Contract

This skill is **read-only over the data layer for synthesis** (passes 1–7), with one v2.3 exception: **Pass 7 connector probe** reads Gmail / Calendar / Slack / Granola for dormant-project re-activation signals and emits passive-capture `interaction` / `meeting` events per `shared/PASSIVE_CAPTURE.md` when hits land. This is a declared passive-capture writer — dedup via `source_ref` hash is mandatory, and writes occur independently of whether Pass 7 surfaces the signal in the insight report.

Also writes `dormancy_reviewed_at` on each dormant project record in `_hq/data/entities.json` to gate the 14-day re-probe cooldown — the only field this skill writes directly to `entities.json`.

The two interactive passes remain the main write paths:

**Pass 8 (classification review) — appends:**
- One `classification_review` event to `_hq/data/events.jsonl` summarizing the review session (reviewed seqs, confirmed/changed/skipped counts).
- One `reclassification` event per user-approved change (with `supersedes_seq`, `old_primary_thread_id`, `new_primary_thread_id`, `old_related_thread_ids`, `new_related_thread_ids`, `reason`). Never mutate a prior event — reclassification is a new append.
- One row per decision to `_hq/data/classifier_feedback.jsonl` (schema: `{ts, event_seq, user_action: "confirmed" | "changed" | "skipped", old_primary, new_primary, signals_used[], confidence_before}`).

**Pass 9 (project proposals) — appends (or delegates):**
- One `project_proposal` event per user action to `_hq/data/events.jsonl` (`{user_action: "created" | "merged" | "declined", fingerprint, new_project_id?, target_project_id?}`).
- One row per decision to `_hq/data/classifier_feedback.jsonl` with `type: project_proposal` so future scoring weights the signals that drove the outcome.
- For `created` and `merged` actions: the underlying `entities.json` mutation and any `reclassification` events are handled by `workspace-manager` via hand-off, not by this skill directly. This skill never writes to `entities.json`.

**Pass 10 (org proposals) — appends (or delegates):**
- One `org_proposal` event per user action to `_hq/data/events.jsonl` (`{user_action: "created" | "merged" | "declined", fingerprint, new_org_id?, target_org_id?}`).
- One row per decision to `_hq/data/classifier_feedback.jsonl` with `type: org_proposal`.
- For `created` and `merged` actions: the underlying `entities.json` mutation is delegated to `org_writer.py create_org / merge_org_into` per `shared/WORKSPACE_API.md`. This skill never writes to `entities.json` directly.

All appends follow `shared/WORKSPACE_API.md` — reserve next seq, append atomically, regenerate affected views (MASTER_TRACKER, TIMELINE), log any failure to `_hq/CONFLICTS.md`.

**Atomic-write requirement (v2.10.5+):** ALL writes to `_hq/data/entities.json` (the `dormancy_reviewed_at` field updates) MUST use `shared/scripts/atomic_write.py atomic_write_json`. ALL appends to `events.jsonl` and `classifier_feedback.jsonl` MUST use `atomic_append_jsonl`. Hand-rolled writes are forbidden — see `shared/WORKSPACE_API.md` § "Write atomically" + § "Append Protocol".

The synthesis output writes to `_hq/insights/[YYYY-MM-DD]_insights.docx` (v3.13.0+ — was `.md` pre-v3.13.0 per CONTRACT Rule 27 "no .md deliverables"; M flagged this in 2026-05-20 feedback #6c). Generated via `shared/scripts/brief_writer.py` `make_brief(brief_kind="insights", ...)` so the document gets canonical Word formatting and opens cleanly. The .docx is surfaced as the canonical H2 heading link at the BOTTOM of the chat turn per CONTRACT Rule 3 (`doc_headline_link()` helper) — NOT interspliced mid-response where it gets lost (M's 2026-05-20 feedback #6d).

### Scope of the synthesis (v3.13.0+ — REQUIRED per M's #6a feedback)

**The output is person, product, and customer insights — NEVER workspace-health / system-meta insights.** Pre-v3.13.0 the skill would surface findings like "your decision log hasn't been refreshed in 9 days" or "12 records have a pending_review flag" — internal-mechanism observations dressed up as "insights." Those belong in cleanup (and even there, per the v3.13.0 reframe, they get translated to plain English). Insight-generator is for OBSERVATIONS ABOUT THE USER'S BUSINESS AND RELATIONSHIPS — what the data shows that the user wouldn't have noticed about their people / projects / customers / market.

Allowed insight categories (every output item must fit one of these):

- **Relationship cadence:** "You haven't talked to [name] in [N] days — that's longer than your usual cadence with them. [Why it might matter — open commitment / pipeline stage / relationship value]."
- **Commitment aging:** "Your commitment to [name] about [topic] is now [N] days old. Either close it or push the date so it stops aging."
- **Project drift:** "[Project name] hasn't had movement in [N] days. Either it's done, paused on purpose, or it's falling between the cracks."
- **Cross-project patterns:** "Both [Project A] and [Project B] are stuck on the same kind of input from [name]. Worth one consolidated ask?"
- **Customer / pipeline signal:** "[Customer name] has gone quiet, but your historical pattern with them shows [their next-touch usually arrives at month-end]. Worth a check-in?"
- **Decision drift:** "You committed to [X] on [date], but recent activity suggests [Y]. Worth re-confirming?"
- **People drift:** "[Person] has been mentioned in [N] meetings without a record in your people graph yet — should they have one?"

**Forbidden insight categories (these belong in other skills, not here):**

- ❌ "Your `events.jsonl` has duplicate seqs" / any substrate-integrity observation. → cleanup.
- ❌ "Pulse hasn't fired in 3 days" / any scheduled-task-health observation. → cleanup.
- ❌ "The renderer is on v3.12.2" / any version observation. → never.
- ❌ "Your workspace folder structure has X issues" / any organization observation. → cleanup.
- ❌ "Schema drift detected on 5 person records" / any validator observation. → cleanup (and even there, translated to plain English).
- ❌ "Your decision log hasn't regenerated" / any system-state observation. → cleanup (and translated).
- ❌ Anything that names a `.json` / `.jsonl` / `.md` file in the user-facing surface.

If M is the user AND M is building the Command Room plugin, the model may be tempted to surface CR-build observations as "insights." Resist that. The insight-generator output is M's business and relationships, NOT M's CR plugin build. (CR plugin build status surfaces elsewhere — morning brief has a dedicated "Internal: Plugin build" subsection per Phase 5's split; cleanup covers system health.)

---

## When it fires

1. **Explicit trigger:** user says "weekly insights", "what am I missing", "run insights", "synthesize the week", "what should I pay attention to", "show me the insight report", "generate insights".
2. **Scheduled:** Sunday 19:00 workspace-local by default. Runs silently; result available on next session. See scheduled-tasks/ configuration.
3. **On demand during briefings:** if `workspace-manager` detects the last insight report is >7 days old during a "what's going on" pass, it offers: "Want me to run the weekly insights while you catch up?"

Do NOT fire on a fresh workspace (<14 days of events). There's not enough data to find patterns. Instead, respond: "Not enough history yet — give me about two weeks of activity and I'll start spotting patterns. So far I have [N] days."

---

## Inputs

**Canonical reads (Tier 1):**

1. `_hq/data/entities.json` — canonical people / threads / orgs
2. `_hq/data/events.jsonl` — raw event stream, last 30 days (use tail reads if large)
3. `_hq/data/classifier_feedback.jsonl` — prior user feedback on classifications (for Pass 8 context + trend tracking)
4. `_hq/briefings/*.md` — last 4 briefings (for delta detection)
5. `_hq/insights/*.docx` — last 4 insight reports (for repeat-pattern suppression). v3.13.0+ writes `.docx` per CONTRACT Rule 27. Pre-v3.13.0 `.md` files (if any) are still read for back-compat — workspace skills may have generated either, depending on the install era.

**Inline-computed projections (v3.12.0+ — REQUIRED, not pre-generated).** Per `references/VIEW_GENERATION.md` and `references/SOURCE_OF_TRUTH.md`, the 5 analytical projections this skill needs are computed inline at the start of each run by aggregating events.jsonl + entities.json — they are NOT read from disk. Pre-v3.12.0 the SKILL.md said "read `_hq/views/TIMELINE.md` (skip if missing)" — but no skill ever generated those view files, so every Pass 1-5 silently degraded to no-input. v3.12.0 makes the computation explicit and Tier 1-sourced:

| Projection | Computed from | Used by |
|---|---|---|
| Chronological timeline (last 30 days) | events.jsonl filtered by `ts` desc, grouped by day, with type-label + confidence-marker | Pass 1, Pass 4 |
| Relationship cadence staleness | entities.json people × last `interaction` / `meeting` event per person, compared to expected cadence | Pass 1 |
| Commitment aging | `cru_match.load_open_commitments` grouped by counterparty + owner, with `(today - due)` days-aged | Pass 2 |
| Dormant threads | entities.json threads × max ts of any event scoped to that thread; dormant if max-ts > 30 days | Pass 5 |
| Theme recurrence | events.jsonl filtered to `theme`-kind threads, count distinct project mentions in last 14 days | Pass 3 |

After synthesis, optionally write the rendered projections to `_hq/views/TIMELINE.md`, `_hq/views/RELATIONSHIPS.md`, `_hq/views/COMMITMENT_AGING.md`, `_hq/views/DORMANT.md`, `_hq/views/THEMES.md` for human-readability — but per `references/SOURCE_OF_TRUTH.md` those files are Tier 2 snapshots, not the source. The next run regenerates from canonical state. Never read these view files as input.

---

## Synthesis passes

Run these passes. Each produces 0-N candidate insights. Rank and filter before output.

### Pass 1 — Stale relationships worth reviving

From RELATIONSHIPS.md "Overdue for Touch" section:
- For each person past cadence, check: was this person previously active (in the last 90 days of events)?
- Have they been dormant before (check previous insight reports)?
- Are they linked to any active high-stakes project (deal in stage 3+, initiative owned by user)?

An insight fires for a person if: overdue by ≥2x cadence AND linked to ≥1 active project AND not flagged in any of the last 2 insight reports.

### Pass 2 — Commitment rot

From COMMITMENT_AGING.md:
- Group by counterparty: any single person owing the CEO ≥3 things?
- Group by project: any project accumulating ≥5 open commitments across both directions?
- Identify "stuck" commitments: open >30 days with no related event activity (no follow-up interaction).

Fires: any person blocking 3+ items, any project with 5+ open, any single commitment stuck 30+ days.

### Pass 3 — Theme recurrence

From THEMES.md + events.jsonl:
- For each active theme, count distinct projects where the theme was mentioned in the last 14 days.
- If ≥3 projects mentioned the same theme, fire an insight: "Theme X surfaced across N projects this week — [list]. Consider whether this is a systemic issue."
- If a theme is referenced but has no dedicated theme project, suggest creating one.

### Pass 4 — Cross-kind collisions

From TIMELINE.md + entities.json:
- Find people appearing in events across ≥3 different projects this week.
- Find dates with ≥2 decisions on similar topics (keyword overlap in decision titles).
- Find deals that reference the same blocker.

Fires: anyone overloaded across 3+ projects, dates where similar decisions happened, shared blockers.

### Pass 5 — Time allocation drift

From events.jsonl:
- Count events per project in the last 7 days vs the prior 7 days.
- Projects with a ≥50% drop in activity AND status==active AND not explicitly paused → "drifting."
- Projects with a ≥100% jump in activity → "surging" (may signal the CEO is over-indexing).
- Compare against stated priorities in `_hq/BUSINESS_CONTEXT.md` if it lists them.

Fires: top 3 drifting active projects, top 3 surging projects if they're not flagged as current focus.

### Pass 6 — Decision drift

From events.jsonl (type=decision) + supersedes_seq chain:
- Find decisions in the last 90 days that have been superseded or revisited ≥2 times.
- Find decisions from the last 30 days that have no follow-up events (decided but no action).

Fires: oscillating decisions, dead-letter decisions.

### Pass 7 — Dormant re-activation candidates

From DORMANT.md + events.jsonl + **live connector probe** (v2.3):
- For each dormant project (no events in ≥30 days, status ≠ archived), run a two-layer check:
  1. **Internal signal:** any recent event (last 14 days, any project) mentions people or keywords from the dormant project — same as before.
  2. **External signal (v2.3 connector probe):** for each dormant project, issue targeted queries against connected tools for the project's people / aliases / domains:
     - **Gmail:** search `from:OR_to:([project's people emails])` AND/OR subject-contains any alias, last 30 days.
     - **Calendar:** any event in the last 30 or upcoming 14 days where the organizer or ≥2 attendees are people on the project.
     - **Slack:** any message in a channel tagged to the project, or any DM from a project person, last 30 days.
     - **Granola:** any unprocessed transcript whose attendee list includes ≥1 project person.
- **Probe discipline:** aggregate 30s budget across all connectors for the full pass (not per-project — the pass can skip remaining projects if budget is exhausted, log `pass7-probe-timeout` to `_hq/logs/scheduled-task-skips.log`). Per-project probe is skipped if the project has `dormancy_reviewed_at` within the last 14 days — avoids weekly re-probing the same cold projects.
- **Emit on probe hits:** every hit becomes a passive-capture `interaction` or `meeting` event per `shared/PASSIVE_CAPTURE.md`, tagged with `primary_thread_id` = the dormant project. This wakes the project in the normal event stream — the CEO sees it in `what's going on` the next morning regardless of whether Pass 7 surfaces it.
- **If internal OR external signal fires:** "Project X is dormant, but [signal — e.g., 'Aria emailed you 3 days ago on thread \"pricing redline\"' or 'calendar shows upcoming meeting with Bowie next Tuesday']. Worth revisiting?"
- **If both layers fire for the same project:** rank higher in Pass 7 output — cross-confirmed signal beats single signal.
- **If neither fires:** no action. Write `dormancy_reviewed_at: [today]` to the project record so the probe doesn't re-run for 14 days.

Fires: dormant projects with a faint pulse from elsewhere in the workspace OR fresh connector traffic that the CEO hasn't noticed.

### Pass 8 — Classification review (batched, silent-by-default)

This is the **only user-interactive pass** in the skill. It exists because silent capture (per `shared/PASSIVE_CAPTURE.md`) deliberately never interrupts the CEO during the week — all provisional and low-confidence classifications are queued here. See `references/ORG_AND_THREAD_MODEL.md` for the full model.

**Scan window:** all events in `events.jsonl` since the last `classification_review` event (or the last 7 days, whichever is shorter).

**Select candidates:**
- `classification_confidence` in `[0.40, 0.75)` → provisional (show summary + top 2 alternative threads; one-key confirm)
- `classification_confidence` < `0.40` → low-confidence (requires explicit assignment)
- Any event where the user previously corrected a similar source/pattern (pulled from `classifier_feedback.jsonl`) → re-surface even if confidence was ≥0.75, because the classifier is newly uncertain.

Cap the review batch at **25 events** per session. If more exist, queue the excess for the next pass.

**Review UX (NOT a wall of prompts).** Render a compact table inside the insight report under its own section. Each row carries a stable action key so the CEO can respond in a single short reply (e.g., "1c, 3 confirm, 5 skip"). Parsing the reply is this skill's responsibility, not the user's.

```
## Classification Review — [N] items

Review: reply with row-# + action (confirm / change to <letter> / skip).
Skipping an item keeps the current classification.

| # | Event | Current project (conf.) | Alt A | Alt B | Action |
|---|-------|------------------------|-------|-------|--------|
| 1 | 2026-04-18 email from rio@example.com ("invoice batch") | Acme Restaurant (0.62) | Category Food Truck | Category Bakery | — |
| 2 | 2026-04-19 meeting "Sam / vendor review" | Acme Co [holding] (0.58) | Acme Restaurant | Category Bakery | — |
...
```

**On the user's reply:**
1. Parse each row instruction.
2. For `confirm`: no event emitted for that row (the confirmation is captured in the aggregate `classification_review` event at the end).
3. For `change to X`: append a `reclassification` event referencing the original `seq`, and append a `classifier_feedback.jsonl` row marking `user_action: "changed"`, signals_used = what the original classifier relied on, so future passes can down-weight that signal.
4. For `skip`: record `user_action: "skipped"` in classifier_feedback.
5. After all rows are processed, append a single `classification_review` event summarizing `{reviewed_event_seqs[], confirmed_count, changed_count, skipped_count}` and triggering view regeneration.

**Confidence trend surfacing (for the insights body, not the review table):** if the last 4 weeks of `classifier_feedback.jsonl` show the user overriding a specific signal repeatedly (e.g., "email domain cluster" wrong >3 times for Acme Co), add a top-level note for the user: "I keep filing [signal] under [org/project] and you keep moving it. Want to help me set up an alias so I get it right next time?" This turns the feedback loop into a visible coaching signal, not a silent tune-up.

**If there's nothing to review** (zero provisional + zero low-confidence events in window): skip the section entirely. Do not render an empty table.

---

### Pass 8 — Edge cases (MUST handle)

Pass 8 is the only interactive pass, so its UX must be defensive. The cases below apply every run.

**A. Zero candidates (no provisional, no low-confidence, no re-surfaced feedback):**
- Skip the entire "Classification Review" section in the output.
- Do NOT append any `classification_review` event. A review didn't happen; claiming one did poisons the learning loop.
- Do NOT render a placeholder table or a "nothing to review" note — the absence of the section is the signal.

**B. >50 candidates in the window (backlog overflow):**
Batch size is capped at 25 per session. When the queue exceeds 50 (i.e., >25 will be deferred):
1. Rank candidates by (a) lowest `classification_confidence` first, (b) events with recurring user-corrected signals next, (c) oldest `ts` as final tiebreaker.
2. Render the top 25 in the table.
3. After the table, add one line: `[N more queued — next review covers them first.]`
4. Write the deferred seqs to `_hq/insights/.review_queue.jsonl` (one `{seq, ts, reason}` per line, overwrite each run). The next Pass 8 reads this file and pulls its candidates from the head of the queue before sampling the window.
5. Log a top-level note for the user: "I have [N] items waiting for your review on how they were filed. Want to do a quick pass — or should I shorten how often I bring these up?"

**C. 30-day neglect (no `classification_review` event in ≥30 days):**
This means the CEO has skipped or missed three-plus weekly passes. Behavior:
1. Widen the scan window to 30 days (not the usual 7).
2. Do NOT dump the full backlog into one table — still cap at 25 per the ranking above, with overflow deferred to `.review_queue.jsonl`.
3. Add a top-of-report callout: "It's been [N] days since you last reviewed how I'm filing things. I've been making my best guesses in the meantime — want to go through a quick pass so I get more accurate?"
4. If still no review after a second 30-day window (60+ days idle), downgrade Pass 8 to one-line summary mode: "I have [N] items I filed on my own without your review. Say 'resume classification review' anytime to go through them with me." This prevents the insight report from being dominated by a wall of stale review rows.

**D. Malformed or ambiguous user reply:**
- Never execute a partial batch on ambiguous input. If the reply contains any row that doesn't parse cleanly (missing action, unknown alt-letter, duplicate row number with conflicting actions, row number out of range), reply: "Couldn't parse: [the problem lines]. Reply with `<row> confirm`, `<row> change to <letter>`, or `<row> skip` — one per row. I'll hold the batch until I hear back." Do NOT emit any events for the good rows — atomicity over partial progress.
- Empty reply / user says "skip all" / user says "done" without instructions → treat as skipping every row (append a `classification_review` with `confirmed_count: 0, changed_count: 0, skipped_count: <N>` and move on).

**E. Stale row (event reclassified by another skill between render and reply):**
- Before processing each row, re-read its event by `seq` from `events.jsonl`. If `primary_thread_id` differs from what the table showed, skip that row and include it in a "stale rows skipped" note at the bottom of the review confirmation. Do not reclassify based on stale evidence.

**F. Duplicate row instructions in one reply:**
- If the same row # appears twice with conflicting actions (e.g., `1 confirm, 1 change to A`), reject the whole reply per case D. If duplicates are identical (`1 confirm, 1 confirm`), accept the first and ignore the rest; do not emit duplicate events.

**G. Unknown alternative letter:**
- If the user writes `3 change to D` but the row only offered A and B, reject per case D. Suggest the available letters in the error message.

**H. Session interrupted before reply:**
- No `classification_review` event is ever emitted speculatively. If the user closes the session mid-review, nothing is persisted — the same candidates surface next run (they'll still be provisional).

**I. Alternative-project ties:**
- If two alternative projects have equal signal strength, render both as "A" and "B" in order of (a) most-recent `last_activity` on the project, (b) alphabetical `display_name`. Never render three alternatives — if more exist, collapse the 3rd+ into a footnote "(+N more; say `expand row <#>` to see)."

**J. No alternatives exist (low-confidence event with no credible alt):**
- Render the row with `Alt A: <create new project>` and `Alt B: <mark as workspace-level / no project>`. The user can then `change to A` to spawn a new project via workspace-manager (this skill hands off, doesn't create projects itself).

**K. Pass 8 failure mid-write:**
- If appending a `reclassification` event fails (disk, schema, lock contention), roll back: do not append the aggregate `classification_review`, discard any partial `classifier_feedback.jsonl` rows, and surface the error in `_hq/CONFLICTS.md` with the reviewed_seqs so the next run retries only those rows. Never leave a partial review in place — the learning loop depends on matched `classification_review` + `reclassification` + feedback triples.

---

### Pass 9 — Project proposals (batched, weekly, interactive)

Pass 8 reviews uncertain classifications of *existing* events against *existing* projects. Pass 9 is the complement: it proposes **new projects** when accumulated signal suggests outcomes will slip without a dedicated anchor. Both passes are batched into the same weekly session — no mid-week interruptions.

**Design principle:** threads are loss-prevention tools. A new project gets proposed only when the CEO is measurably worse off *without* one — not every time a new entity shows up.

**Scan window:** last 30 days of `events.jsonl`, bounded by the last `project_proposal` event (if any) to avoid re-proposing candidates that were just acted on.

#### Candidate signals

Four **primary triggers** — each, alone, can fire a proposal candidate:

1. **Recurring cadence without a home.** A recurring calendar event (accepted by CEO) with a new counterparty or topic, where the recurrence instance is not already attached to an existing project. Weight: 4.
2. **Commitment density at an orphan entity.** 2+ commitments or decisions captured against the same person, vendor, or org that has no project. Detected from events where `type: commitment` or `type: decision` and `primary_thread_id: null` cluster around the same `stakeholder_person_ids` or org. Weight: 3 per commitment (capped 9).
3. **CEO organizational behavior.** CEO created a new Gmail label, Slack channel, Drive folder, or renamed a calendar series with a project-like identifier (token matching `[A-Z][a-z]+ ?(project|initiative|deal|rollout|launch)` OR matches an MSA/SOW/quote code pattern). Weight: 5.
4. **Counterparty treats it as a project.** Inbound doc/email references an MSA, SOW, "Project [X]", quote number, contract ID, or recurring report title. Weight: 4.

**Stacking signals** (never fire alone — they add weight to an already-firing primary):

- 3+ orphan events over 7 days mentioning same entity (weight: 1 per orphan event, capped at 4)
- New high-engagement person (3+ two-way email/Slack exchanges in 14 days) (weight: 2)
- Dollar figures or financial terms recurring against same entity (weight: 2)
- Multiple team members independently discussing same thing across different threads (weight: 2)

**Penalties:**

- Proposal fingerprint in cooldown (proposed + declined within last 60 days): -5 (effectively suppresses)
- Existing project could absorb via cross-ref (high semantic + people overlap with a known project): -3 (shifts toward merge-not-create)

**Fire threshold:** score ≥ 8.

#### Proposal fingerprint (for cooldown tracking)

```
fp = sha256(
  sorted(involved_person_ids) + "|" +
  sorted(involved_org_ids) + "|" +
  normalized_proposed_name
)
```

Store fingerprints + decline decisions in `_hq/data/classifier_feedback.jsonl` with `type: project_proposal`. The 60-day cooldown window is measured from the decline timestamp.

#### Cap and ranking

Render **at most 3 proposals per weekly review**. If more candidates score ≥ 8:

1. Rank by score descending.
2. Break ties by (a) presence of recurring cadence signal, (b) freshness of the strongest signal.
3. Top 3 render. Remaining queued to `_hq/insights/.proposal_queue.jsonl` for next week.

The 3-cap is a deliberate UX constraint. Overwhelming the CEO with 10 new-project prompts at once kills adoption faster than missing a project.

#### Merge before create

For each proposal, also compute the best-match existing project (if any) using people overlap + semantic similarity of recent event summaries. If a candidate exists with match score ≥ 0.5, render a **Merge** option alongside the **Create** option. Often the right answer is "this is actually an extension of the Property Alpha job" — merge beats create.

#### Review UX — render under its own section

```
## Proposed Projects — [N]

Review: reply with row-# + action (create / merge / ignore).
Ignored proposals enter a 60-day cooldown.

| # | Proposed name | Signal | People | Merge candidate | Action |
|---|---------------|--------|--------|-----------------|--------|
| 1 | ABC Supplier contract | recurring weekly call + 2 commitments | Rio, Avery | Acme Restaurant | — |
| 2 | Q3 pricing revision | CEO created Slack #pricing-q3 + 5 exchanges | Sam, Rio | (no match — create only) | — |
| 3 | [Client] property walkthrough series | recurring Thu calls, 3 sessions logged | [contact], [contact] | Acme Property › Property Alpha | — |
```

Reply format examples: `1 merge`, `2 create`, `3 ignore` — one line per row, comma or newline-separated.

#### On the user's reply

For `create`:
1. Hand off to `workspace-manager` with payload: `{action: "create_project", display_name, kind: inferred, affiliation_id: best_guess_org, stakeholder_person_ids: involved_people}`.
2. workspace-manager creates the project record in `entities.json` and returns the new `project_id`.
3. Back in this skill: append a `project_proposal` event (`{user_action: "created", new_project_id, fingerprint}`) and a `classifier_feedback.jsonl` row with `type: project_proposal, user_action: "created"`.
4. The prior orphan events that fed the proposal are then eligible for reclassification in *next week's* Pass 8 — we don't auto-reclassify them now. (Separation of concerns: Pass 9 creates the container, Pass 8 fills it.)

For `merge`:
1. Hand off to `workspace-manager` with payload: `{action: "reclassify_events", seqs: [the orphan event seqs that fed the proposal], new_primary_thread_id: merge_target}`.
2. workspace-manager appends `reclassification` events (one per seq) and updates `last_activity` on the target.
3. Back in this skill: append a `project_proposal` event (`{user_action: "merged", target_project_id, fingerprint}`) and a `classifier_feedback.jsonl` row.

For `ignore`:
1. Append a `project_proposal` event (`{user_action: "declined", fingerprint, reason: null}`).
2. Append a `classifier_feedback.jsonl` row with `type: project_proposal, user_action: "declined", fingerprint`.
3. Fingerprint now carries a 60-day cooldown enforced by future Pass 9 scans.

#### Pass 9 — Edge cases (MUST handle)

**A. Zero proposals (no candidates cross the threshold):**
Skip the section. Do not render an empty table. Do not emit a `project_proposal` event — a proposal didn't happen.

**B. Proposal fingerprint is in cooldown:**
Silently suppressed from the candidate list at scoring time. Does not consume one of the 3 slots. Cooldown decrements naturally as time passes; no manual reset needed.

**C. >3 candidates score ≥ 8:**
Render top 3 by score; queue remaining to `_hq/insights/.proposal_queue.jsonl` (one `{fingerprint, score, candidate_summary, ts}` per line, overwrite each run). Top-level note for the user: "I spotted [N] possible new projects this week and showed you the top 3 — the rest will surface next week. Looks like things are accelerating."

**D. Malformed user reply:**
Same atomicity rule as Pass 8 — if any row doesn't parse cleanly, reject the whole reply with: "Couldn't parse: [lines]. Reply with `<row> create`, `<row> merge`, or `<row> ignore` — one per row." Emit no events.

**E. workspace-manager hand-off fails (for create or merge):**
Rollback: do NOT append the `project_proposal` event, do NOT append the `classifier_feedback.jsonl` row. Surface in `_hq/CONFLICTS.md` with the fingerprint + attempted action. Next run will re-propose (fingerprint never entered cooldown because action didn't commit).

**F. User says "create" but candidate has a strong merge target:**
Honor the user. Do not override or ask again. The learning loop via `classifier_feedback.jsonl` will note that the signal was correct (proposal fired) even though the merge suggestion was overridden — down-weights the merge-candidate scorer for similar future candidates with the same org cluster.

**G. Two proposals resolve to the same underlying entity:**
Before rendering, de-duplicate candidates whose fingerprints match (same people + same normalized name) or whose merge targets are identical. Keep the highest-scoring one; discard duplicates silently.

**H. Proposal for a person-only relationship (no org anchor):**
If a proposal is driven primarily by "high-engagement person" signals and has no org affiliation, render the `Merge candidate` column as `(person-relationship — people-crm may be the right home)`. This nudges the CEO to consider whether the right container is a project or just a people-crm relationship record. The create action still works but hints at the lighter-weight alternative.

**I. Session interrupted before reply:**
No `project_proposal` event emitted speculatively. Candidates re-surface next run (fingerprints re-scored; cooldown doesn't start until a decline is committed).

**J. Proposal name inference fails (can't generate a clean name):**
Render the row with `Proposed name: [unnamed — derive from: <signal summary>]`. The CEO can still act (create will prompt them for a name at workspace-manager's end; merge doesn't need one).

**K. Cross-week continuity — same fingerprint proposed twice:**
If a fingerprint appears in two consecutive weekly reviews without action (neither created nor declined), the second appearance carries a `(unresolved from last week)` tag. Third consecutive appearance auto-demotes the score (−2) so the proposal falls below threshold and enters a soft cooldown — the CEO's inaction is a weak decline.

---

### Pass 10 — Org proposals (batched weekly + high-confidence daily, v2.10.3+)

Pass 9 proposes **projects** when accumulated signal suggests outcomes will slip. Pass 10 is the complement for **orgs** — when an email-domain cluster, Slack workspace, or recurring counterparty surfaces in connector signals without an existing org record, this pass proposes creating one with an inferred `relationship_type`.

**Design principle:** orgs are the workspace's spine — the org tree decides where every project lands and how every event gets routed. A new org gets proposed only when accumulated signal suggests the user will start losing context without one.

**Scan window:** last 30 days of `events.jsonl`, bounded by the last `org_proposal` event in `classifier_feedback.jsonl` (60-day cooldown per fingerprint).

#### Candidate signals

Four primary triggers (each fires alone):

1. **New email-domain cluster.** 5+ emails to/from a domain not in any existing org's `domains[]`. Weight: 5.
2. **New Slack workspace or Teams tenant.** CEO joined a workspace not tied to any known org. Weight: 5.
3. **New SharePoint site.** CEO got access to a site not under any known org. Weight: 4.
4. **Recurring counterparty in transcripts.** Granola transcripts mention an organization name 3+ times across different meetings, no matching org record. Weight: 4.

**Stacking signals:**

- Person from new domain attended 3+ meetings in 14 days: +2
- Drive doc shared with new domain: +1
- Signature block found with org name + title: +2
- Sales/contract language ("MSA", "SOW", "quote", "proposal", "scope"): +2 (signals prospect)
- Recurring billing/invoice signal in mail: +2 (signals service_provider)
- Reciprocal volume (CEO sent N, received ~N back): +1 (signals client / partner / advisor)

**Penalties:**

- Proposal fingerprint in cooldown (60 days from last decline): -5
- Domain matches an existing org's alias: -3 (likely just an alternate domain — prefer adding to existing org)

**Fire threshold:** score ≥ 7.

#### Relationship_type inference from signal pattern

Once a candidate fires, infer `relationship_type` from the signal mix:

| Signal pattern | Inferred relationship_type |
|---|---|
| Mostly inbound + generic email + 1-off purchase signals | `vendor` |
| Mostly outbound + recurring meetings + sales language | `prospect` |
| Mostly outbound + recurring meetings + paid engagement signals (POs, invoices CEO sent) | `client` |
| Reciprocal volume + senior-name signature + advisor language | `advisor` |
| Reciprocal volume + co-decision signals | `partner` |
| Recurring billing + service-provider language ("subscription", "monthly fee", "support") | `service_provider` |
| Cannot infer from signal | `other` (with relationship_label set to one-line description of what the signal looked like) |

**Default tier:** all Pass 10 proposals default to `tier: external`. The user can promote to `secondary` (for active client / partner / advisor) or `primary` (for owned operating units) via the confirmation action set.

#### Proposal fingerprint (cooldown tracking)

```
fp = sha256(
  normalized_domain_or_workspace_id + "|" +
  inferred_relationship_type
)
```

Store in `_hq/data/classifier_feedback.jsonl` with `type: org_proposal`. 60-day cooldown from decline timestamp.

#### Cap and ranking

Render at most 3 proposals per weekly review (same as Pass 9). If more candidates score ≥7, queue overflow to `_hq/insights/.org_proposal_queue.jsonl`.

#### Surfacing cadence — both daily and weekly

Per the v2.10.3 audit decision (M's confirmation: "both"):

- **Daily — Pulse surfaces high-confidence proposals (≥3 strong signals stacked, score ≥10).** Same `a/b/c confirm/edit/skip` action set as the people-record review block. Pulse reads pending org proposals from `_hq/insights/.org_proposal_queue.jsonl` + any from this run that scored ≥10. Daily surfacing makes obvious new orgs (a new client lands hard) get attention same-day rather than waiting for Sunday.
- **Weekly — insight-generator's regular Sunday run renders all remaining ≥7 candidates** in the standard 3-cap review section. Lower-confidence proposals batch here.

Same fingerprint across both surfaces — if surfaced daily and acted on, weekly review skips it (cooldown applies). If surfaced daily and ignored, weekly review will re-surface (with `(unresolved from earlier this week)` tag).

#### Review UX — render under its own section

```
## Proposed Orgs — [N]

Review: reply with row-# + action (create / edit [type] / ignore).
Ignored proposals enter a 60-day cooldown.

| # | Inferred name | Inferred type | Signal | Tier | Action |
|---|---------------|---------------|--------|------|--------|
| 1 | Acme Logistics | vendor (4 emails, 1 invoice) | 1-off purchase | external | — |
| 2 | Acme Co | client (12 emails, 3 meetings) | recurring + reciprocal | secondary | — |
| 3 | Northstar Partners | advisor (8 emails, 2 board references) | board language | secondary | — |
```

Reply format examples: `1 create`, `2 edit client → partner`, `3 ignore`.

#### On the user's reply

For `create`:
1. Hand off to `workspace-manager` with payload: `{action: "create_org", canonical_name, relationship_type, tier, domains, inferred_from: ["pass_10_proposal"]}`.
2. workspace-manager creates the org record in `entities.json` and returns the new `org_id`.
3. Append an `org_proposal` event (`{user_action: "created", new_org_id, fingerprint}`) and a `classifier_feedback.jsonl` row with `type: org_proposal, user_action: "created"`.
4. The orphan events that fed the proposal (emails, meetings tagged to the new domain) are now eligible for re-tagging in next week's Pass 8.

For `edit [type]`:
- Same as `create` but with the user-chosen `relationship_type` overriding the inferred one. Logs the override to `classifier_feedback.jsonl` so the inference improves over time.

For `ignore`:
- Append `org_proposal` event (`{user_action: "declined", fingerprint}`). 60-day cooldown.
- For `ignore (just noise)` variant: same cooldown but adds a `permanent_noise: true` flag — fingerprint never re-surfaces unless the signal pattern changes substantially.

#### Pass 10 — Edge cases (MUST handle)

**A. Zero candidates:** skip the section. Don't emit an `org_proposal` event.

**B. Cooldown:** silently suppress. Doesn't consume a slot.

**C. >3 candidates score ≥7:** top 3 render, rest queue to `.org_proposal_queue.jsonl`.

**D. Malformed reply:** atomic reject, same as Pass 9.

**E. workspace-manager hand-off fails:** rollback, surface in `_hq/CONFLICTS.md`.

**F. Daily surfacing in Pulse conflicts with weekly review:** same fingerprint won't surface in both passes. Pulse marks the proposal as `surfaced_daily: true` in events.jsonl on first daily appearance. insight-generator skips already-`surfaced_daily` proposals.

**G. Domain looks like an alias of an existing org:** suppress with -3 penalty AND surface a top-level note: "I see [N] emails to/from [new_domain]. Looks like it might be the same as [existing_org] — want me to link them?" One-line suggestion separate from the proposal table.

**H. Proposed org would create a duplicate:** dedupe at proposal time. If two candidates (different signal sources) point to the same domain or workspace_id, merge to one proposal with combined signals.

---

## Ranking

Each candidate insight gets a score:

- **Urgency** (0-3): how time-sensitive. Commitment stuck 60 days = 3. Theme recurring = 1. Dormant relationship = 2.
- **Novelty** (0-3): not flagged in last 2 insight reports. Brand new observation = 3. Second mention = 1. Third mention = 0 (drop).
- **Specificity** (0-3): concrete people/threads/dates named. Vague pattern = 1. Three-name specific = 3.

Total score = sum. Report the top 7-10 insights. Discard anything <5 total.

---

## Output format (v3.13.0+ — .docx deliverable + plain-English voice)

**Path:** `_hq/insights/YYYY-MM-DD_insights.docx` (v3.13.0+ — was `.md` pre-v3.13.0 per #6c).

**Generation:** via `shared/scripts/brief_writer.py make_brief(brief_kind="insights", title=..., sections=[...])` — same canonical Word formatting as every other generated deliverable. Per CONTRACT Rule 27 (no .md deliverables), insights are now a polished .docx.

**Chat surface:** the chat post for insight-generator follows the friendly-voice contract. Lead with a one-line summary of what the week showed. Then surface the top 3-5 insights INLINE in chat (so M reads them without opening the doc). Then the canonical H2 deliverable link at the BOTTOM of the chat turn pointing to the full .docx for the deeper version. No file path interspliced through the prose body; no `entities.json` / `events.jsonl` / `project_NNN` leaks; no internal mechanism names ("Pass 7 probe found", "classification review"); no scores or grades on the user's business.

**Document structure (.docx body, also follows the voice contract):**

```
# Insights — [Week of Mon DD]

[Lead — one paragraph in human voice. Examples:]

"Two threads moved this week — Acme Restaurant came back to life after 9 quiet days, and a pricing question started showing up across three different conversations. Everything else is on track."

"Quiet week — no aging commitments, three regular relationships at their usual cadence, Northstar back from a brief lull. Nothing pressing; here are a few small things if you want them."

## What's worth a closer look

[2-5 items. Each item:]

**[Headline — what the pattern IS, in one sentence]**

[2-3 sentences of context: who, what, when, why it matters.]

[Specific next-action suggestion — one sentence, concrete, not abstract.]

---

## Smaller observations

[3-7 lighter items as a bulleted list. Each one line. Things worth noticing but not worth a paragraph.]

## What's on track

[1-3 short lines noting projects / relationships that are at their usual cadence. The absence of an insight is information too — surface it explicitly so M doesn't wonder "did the skill miss X?"]
```

**Pass 8 (classification review) and Pass 9 (project proposals) — separate widgets, not embedded in the .docx body.** Pre-v3.13.0 the classification review and project proposals were rendered as tables inside the insight report. v3.13.0+: surface those as separate widgets per the standard chat-action-widget contract (with `confirm` / `edit [text]` / `skip` actions per CANONICAL_ACTIONS) so the user can act on them directly. The .docx itself contains observations + suggestions, not interactive surfaces.

**Forbidden in the .docx body and the chat surface (per universal voice contract):**

- ❌ "Window: 2026-05-13 to 2026-05-20 · Events analyzed: 247" — surface this as "Looking back at this week" in prose, not as metadata
- ❌ "Confidence: 0.87 · Signals: 3" — drop the score; if signal strength matters, frame in prose ("strong signal" / "worth a look but I'm not sure")
- ❌ "person_072 surfaced in 4 events with project_020" — use names ("Elan came up in 4 conversations about Dynarii")
- ❌ Section headers like "STABLE / ROUTINE" — use sentence case ("What's on track")
- ❌ "Classification Review — N items" as a doc section header — split off into a separate interactive widget
- ❌ Any reference to `.json` / `.jsonl` / `_hq/` paths in the user-facing prose

---

## Skill Boundary (What This Does Not Do)

- **Read-only over the data layer for synthesis passes (1–7).** The exceptions are: Pass 8 (classification review), which appends `classification_review` + `reclassification` events and `classifier_feedback.jsonl` rows; Pass 9 (project proposals), which appends `project_proposal` events and `classifier_feedback.jsonl` rows (and delegates the actual `entities.json` mutation to workspace-manager for create/merge actions); and Pass 10 (org proposals, added in v3.12.0+), which appends `org_proposal` events and `classifier_feedback.jsonl` rows of `type: org_proposal`. Synthesis output (Passes 1–7) lands in `_hq/insights/<date>.docx` per v3.13.0+ (CONTRACT Rule 27 — no .md deliverables). Pass 8 and Pass 9 outputs surface as interactive widgets in the chat turn — NOT as separate .md files (pre-v3.13.0 the SKILL.md said they wrote to `_hq/insights/REVIEW_<date>.md` and `_hq/insights/PROPOSED_PROJECTS_<date>.md`; v3.13.0+ they're widgets, with the user's choices captured into the events above).
- **Does not answer ad-hoc questions.** "Show me Bowie's recent activity" → workspace-manager, not insight-generator.
- **Does not handle meeting prep.** → call-prep.
- **Does not draft outreach.** Suggests actions but doesn't compose emails. The CEO sees the insight, then triggers follow-up-ritual or one-pager-composer as needed.
- **Does not run on a fresh workspace.** Requires ≥14 days of events to find patterns.
- **Does not suppress insights the user wants to see again.** If the user says "tell me about this again next week," the skill adds the project to an allowlist in `_hq/insights/.allowlist.md` that overrides the repeat-suppression rule.

---

## Quality principles

1. **Specific beats general.** "Three customers mentioned pricing this week — Acme, Northstar, and Cascade" beats "pricing is coming up a lot."
2. **Actionable beats descriptive.** Every top insight ends with a suggested next step the CEO could execute today.
3. **Novelty matters.** If an insight appeared in the last report and nothing's changed, demote it. Repeat only if the data strengthened.
4. **Honest about empty.** If the week had no real patterns worth surfacing, say so. Don't manufacture insights to fill space. A three-line "nothing structural to flag this week" is better than padded fluff.
5. **No generic advice.** "Consider delegating more" is banned. Every suggestion must reference specific threads, people, or commitments from the workspace.

---

## Schedule Configuration

When scheduled-tasks is available, register:

```
name: weekly-insights
cadence: weekly
day: sunday
time: 19:00
action: invoke insight-generator
silent: true  # don't interrupt; drop output for next session
```

On the next session after a scheduled run, workspace-manager surfaces: "Your weekly insights from [date] are ready — want to see them?"

---

## Cross-skill handoff

- **morning-briefing** can link to the latest insight report in its opening.
- **workspace-manager** offers to run insights if none in past 7 days during a "what's going on."
- **cleanup** flags if insight-generator hasn't run in >14 days (schedule may be broken).
- **one-pager-composer** may be invoked by the CEO in response to an insight ("turn #3 into a note to Sam").

---

## Reliability

This skill runs as a scheduled task (Sunday 19:00, intentional — ready for Monday) and must implement `shared/RELIABILITY.md`. Key rules: skip-not-fail when workspace has <14 days of events or no BUSINESS_CONTEXT.md (log to `_hq/logs/scheduled-task-skips.log`, exit clean, never produce empty insight reports), OOO defers until 2 days after return date, missed-fire recovery reruns once if missed fire was <7 days ago otherwise defers to next scheduled fire, 60s aggregate scan budget across all connectors with graceful degradation, dedup via `source_ref` hash ensures re-running the skill is idempotent (silent no-op on duplicate events). Pass 8 (classification review) and Pass 9 (project proposals) surface as interactive widgets in the chat turn — the user's choices land as `classifier_feedback.jsonl` rows + (for accepted proposals) `entities.json` mutations via workspace-manager. Pre-v3.13.0 these passes wrote to `_hq/insights/REVIEW_<date>.md` and `_hq/insights/PROPOSED_PROJECTS_<date>.md` as a delivery fallback; v3.13.0+ the widget IS the delivery surface and no separate file is written.

## What It Doesn't Do

- Does not write to entities.json directly. Writes to events.jsonl only via Pass 8 (classification review) and Pass 9 (project proposals), both of which require explicit user action on a review table — never silently. For Pass 9 create/merge actions, `entities.json` mutation is delegated to workspace-manager.
- Does not mutate prior events. Reclassification = new append with `supersedes_seq`, per schema.
- Does not propose projects on a fresh workspace. Pass 9 inherits the ≥14-day minimum from the skill's overall gate; Pass 9 itself requires ≥30 days of data before proposing, since cadence signals need time to materialize.
- Does not answer one-off questions ("why is NorthStar stuck?") — that's `workspace-manager` with connector context.
- Does not produce generic advice — only workspace-grounded observations. If the workspace has <14 days of events, the skill exits cleanly without producing an insight report.
- Does not send or act on insights — every insight is a prompt for CEO attention, never an auto-action.
- Does not interrupt mid-week to ask about classifications — that's the whole point of Pass 8 being batched and weekly.
- Does not replace the cleanup — audit checks workspace health; insight-generator synthesizes patterns.
