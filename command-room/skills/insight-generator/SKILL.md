---
name: insight-generator
surfaces: both
description: "Weekly synthesis pass that surfaces patterns the CEO wouldn't compute themselves and runs the product's learning reviews. Fires on: 'weekly insights', 'run insights', 'generate insights', 'what am I missing', 'synthesize the week', 'what should I pay attention to', 'cross-project patterns', 'what's drifting', 'show me the insight report', 'review project proposals' / 'new project proposals', 'review classifications', and as the maintenance task's Sunday insights job. Produces the analytical views (timeline, relationships, commitment aging, dormancy, themes) plus batched confirm/edit/skip proposals from the learning passes. Does NOT fire on 'list projects' (list-active) or coaching asks like 'what should I focus on' (command-room-coach). Pass-by-pass detail and fences: Routing section in the body."
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

**Pass 11 (voice calibration review, B1) — appends:**
- One `voice_block_updated` event per approved proposal (`{skill, change_summary, correction_count, fingerprint}`); the refreshed block is written to `_hq/voice/voice-block-<skill>.md` via `voice_corrections.write_voice_block_override` (workspace-side — NEVER the plugin SKILL.md).
- One `voice_calibration_review` event at end of pass (`{reviewed_through: {<skill>: <max-ts>}, proposed, approved, skipped}`).

**Pass 13 (sender-priority proposals, Phase 6 Loop 1) — appends:**
- One `sender_priority_proposal` event per user action (`{user_action: "applied" | "edited" | "declined", fingerprint, sender_or_domain}`).
- The approved rule is written to `_hq/data/sender-priority-rules.json` via `triage_feedback.write_sender_priority_rules` (workspace-side). Decision + cooldown to `_hq/data/proposal_feedback.jsonl` via `proposal_ledger.append_decision`.

**Pass 14 (surface-preference proposals, Phase 6 Loop 2) — appends:**
- One `surface_preference_proposal` event per user action (`{user_action, fingerprint}`).
- The approved suppression is written to `_hq/data/surface-preferences.json` via `surface_preferences.write_surface_preferences`. Decision + cooldown to `_hq/data/proposal_feedback.jsonl`.

**Loop 4 (confidence calibration, Phase 6 Round 2) — appends:**
- One `confidence_override_proposal` event per user action; the approved threshold is written to `_hq/data/confidence-overrides.json` via `confidence.write_overrides` (read back by `confidence.py` accessors). Decision + cooldown to `proposal_feedback.jsonl`.

**Loop 6 (chase policy, Phase 6 Round 2) — Pass 7b propose-and-apply — appends:**
- One `chase_policy_proposal` event per user action; the approved group is written to `_hq/data/chase-policy.json` via `chase_policy.write_chase_policy`. Decision + cooldown to `proposal_feedback.jsonl`.

**S3 rider (commitment noise, Phase 6 Round 2) — appends:**
- One `commitment_noise_proposal` event per user action; the approved rule appends to `_hq/config/commitment-rules.md` via `commitment_noise.append_never_track_rule` (additive; the capture floor reads it). Decision + cooldown to `proposal_feedback.jsonl`.

**Pass 15 (prep section weights, Phase 6 Loop 3) — appends:**
- One `prep_weight_proposal` event per user action; the approved weight is saved to the call-prep skill config (`skill_config_writer.save_skill_config`). Decision + cooldown to `proposal_feedback.jsonl`.

**Loop 5 (extraction hints, Phase 6 Round 3) — appends:**
- One `extraction_hint_proposal` event per user action; the approved hint appends to `_hq/data/extraction-hints.md` via `extraction_hints.append_extraction_hint`. Decision + cooldown to `proposal_feedback.jsonl`.

**Pass 16 (exemplar structure review, SPEC OUT8) — appends:**
- One `exemplar_update_proposal` event per user action; the approved skeleton is written to `_hq/exemplars/<kind>/exemplar_1.md` via `exemplars.promote_workspace_exemplar` (workspace-side — NEVER the plugin's shipped seeds; the previous version rotates to `exemplar_2.md`; the scrub gate replaces entity names with placeholders and re-runs the leak scan before the write — residual findings REFUSE the write). Decision + cooldown to `proposal_feedback.jsonl`.

All appends follow `shared/WORKSPACE_API.md` — append atomically via `atomic_append_jsonl` (omit `seq`; the appender allocates it inside the writer lock — never pre-compute, BUG-8330 item 7), regenerate affected views (MASTER_TRACKER, TIMELINE), log any failure to `_hq/CONFLICTS.md`.

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
- **People drift:** "[Person] has come up in [N] meetings but isn't in your contacts yet — want me to add them?"
- **Outcome patterns:** "Your investor emails get a reply in about [N] days; vendor threads go quiet [X]% of the time." Reply rate + median latency grouped by recipient relationship type, plus your own commitment punctuality (on-time vs late). Read-only over the `email_outcome` events; names groups, never whether one individual replied.

**Forbidden insight categories (these belong in other skills, not here):**

- ❌ "Your `events.jsonl` has duplicate seqs" / any substrate-integrity observation. → cleanup.
- ❌ "The staff meeting hasn't fired in 3 days" / any scheduled-task-health observation. → cleanup.
- ❌ "The renderer is on v3.12.2" / any version observation. → never.
- ❌ "Your workspace folder structure has X issues" / any organization observation. → cleanup.
- ❌ "Schema drift detected on 5 person records" / any validator observation. → cleanup (and even there, translated to plain English).
- ❌ "Your decision log hasn't regenerated" / any system-state observation. → cleanup (and translated).
- ❌ Anything that names a `.json` / `.jsonl` / `.md` file in the user-facing surface.

If M is the user AND M is building the Command Room plugin, the model may be tempted to surface CR-build observations as "insights." Resist that. The insight-generator output is M's business and relationships, NOT M's CR plugin build. (CR plugin build status surfaces elsewhere — morning brief has a dedicated "Internal: Plugin build" subsection per Phase 5's split; cleanup covers system health.)

---

## When it fires

1. **Explicit trigger:** user says "weekly insights", "what am I missing", "run insights", "synthesize the week", "what should I pay attention to", "show me the insight report", "generate insights".
2. **Scheduled:** a job inside the `maintenance` background task (MAINT1) — due at the Sunday 5:45 PM fire, ordered AFTER the cleanup job so synthesis reads a settled substrate; a missed Sunday self-heals at the next fire. Runs silently; result available on next session.
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
| Commitment aging | `cru_match.load_open_commitments` → `cru_match.split_pending_review(...)`, **confirmed half only** (INTAKE — an unconfirmed extraction has no age worth reporting; ageing a guess manufactures a problem), grouped by counterparty + owner, with `(today - due)` days-aged | Pass 2 |
| Dormant threads | entities.json threads × max ts of any event scoped to that thread; dormant if max-ts > 30 days | Pass 5 |
| Theme recurrence | events.jsonl filtered to `theme`-kind threads, count distinct project mentions in last 14 days | Pass 3 |

After synthesis, write the rendered projections to `_hq/views/TIMELINE.md`, `_hq/views/RELATIONSHIPS.md`, `_hq/views/COMMITMENT_AGING.md`, `_hq/views/DORMANT.md`, `_hq/views/THEMES.md`. `COMMITMENT_AGING.md` may carry the queue as ONE labelled pointer line at the foot — mirror the in-code precedent from `render_master_tracker`: *"N unconfirmed — not counted as open commitments above; say `needs your call`"* — never as aged rows. **On scheduled fires these writes are MANDATORY — keeping the Tier 2 snapshots fresh is part of the weekly task's job** (an "optional" write on a scheduled fire is how views freeze — the v4.2.0 frozen-views class). On explicit-trigger runs they're optional. Per `references/SOURCE_OF_TRUTH.md` the view files remain Tier 2 snapshots, not the source: the next run regenerates from canonical state, and this skill never reads them as input.

---

## Synthesis passes

Run these passes. Each produces 0-N candidate insights. Rank and filter before output.

### Pass 1 — Stale relationships worth reviving

From the inline relationships projection (the "Overdue for Touch" slice, computed per the v3.12.0 block above — never the view file):
- For each person past cadence, check: was this person previously active (in the last 90 days of events)?
- Have they been dormant before (check previous insight reports)?
- Are they linked to any active high-stakes project (deal in stage 3+, initiative owned by user)?

An insight fires for a person if: overdue by ≥2x cadence AND linked to ≥1 active project AND not flagged in any of the last 2 insight reports.

### Pass 2 — Commitment rot

From the inline commitment-aging projection (computed per the v3.12.0 block above):
- Group by counterparty: any single person owing the CEO ≥3 things?
- Group by project: any project accumulating ≥5 open commitments across both directions?
- Identify "stuck" commitments: open >30 days with no related event activity (no follow-up interaction).

Fires: any person blocking 3+ items, any project with 5+ open, any single commitment stuck 30+ days.

### Pass 3 — Theme recurrence

From the inline themes projection + events.jsonl:
- For each active theme, count distinct projects where the theme was mentioned in the last 14 days.
- If ≥3 projects mentioned the same theme, fire an insight: "Theme X surfaced across N projects this week — [list]. Consider whether this is a systemic issue."
- If a theme is referenced but has no dedicated theme project, suggest creating one.

### Pass 4 — Cross-kind collisions

From the inline timeline projection + entities.json:
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

From the inline dormancy projection + events.jsonl + **live connector probe** (v2.3):
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

### Pass 7b — Outcome patterns (read-only, B6)

Reply-rate + median-latency patterns from the `email_outcome` events (written silently by reconcile-sent's outcome watch), plus the user's own commitment punctuality. READ-ONLY — this pass writes no events.

- **Compute** via `shared/scripts/email_outcomes.py`: reply rate and median `latency_days` grouped by the recipient's org `relationship_type` (recipient → person → org), and `commitment_punctuality(events, as_of_iso=...)` for the on-time/late split on the user's own commitments.
- **Fire floors (small-n honesty — Quality principle 4):** surface this pass ONLY with **≥ 8 terminal outcomes** in the trailing 30-day window, and name a group ONLY with **≥ 3 outcomes in that group**. Below the floor, say nothing — a "100% reply rate" off n=2 is noise, and these numbers feed ROI receipts.
- **Voice:** allowed-category phrasing, named groups not individuals. Example: *"Your investor threads reply in about 2 days; vendor threads go quiet roughly 40% of the time — worth front-loading the asks that matter there."* Punctuality example: *"You're closing about 7 in 10 of your own commitments on time."*
- **Writes nothing in report mode.** The `email_outcome` events already exist; the read-and-report half writes nothing.

**Pass 7b propose-and-apply (Phase 6 Loop 6 — chase policy).** When the report floors are met, Pass 7b ALSO proposes per-relationship-type chase windows via `shared/scripts/chase_policy.py`, so follow-ups fire when they actually get answered instead of on a fixed 7-day cadence. `load_email_outcomes(workspace_root, since_iso=<30d>)` → `group_outcomes(rows, relationship_of)` (resolve recipient → person → org.relationship_type) → `derive_chase_policy(groups, existing_policy=<store>, cooldown_fingerprints=<cooldowns>, cap=3)`. Same small-n floors as the report (**≥8 terminal total, ≥3 per group**). Render each proposal as a REVIEW item (`confirm`/`edit [change]`/`skip`): *"Your vendor threads go quiet ~40% of the time — chase at day 3 instead of 7, and suggest a call after 2 silent chases?"*. On `confirm`, write the group via `group_from_proposal` into `_hq/data/chase-policy.json` (`load_chase_policy` → set group → `write_chase_policy`), append a `chase_policy_proposal` event, and log the decision to `proposal_ledger` (`loop6_chase_policy`, 60-day cooldown). Consumers: the commitments orchestrator reads `get_chase_window(policy, rtype)` when bucketing; email-writer reads it for follow-up timing/escalation. Atomic-reject + rollback exactly as Pass 9.

Fires: a workspace with ≥ 8 terminal email outcomes in the 30-day window; otherwise skipped.

### Pass 7c — Capture-gate tuning proposals (v4.6.1 S3, W4c follow-up — PROPOSE only, the gate never self-adjusts)

The W4c relevance gate accumulates the user's Not-mine / Drop / dismiss verdicts as labeled corrections; this pass turns them into consented tuning. ONE call:

```python
from capture_gate import propose_gate_directives, apply_gate_proposal
proposals = propose_gate_directives("<WORKSPACE>",
    cooldown_fingerprints=<proposal_ledger declined fingerprints>)
```

The helper mines `commitment_resolved` (dropped/not-mine), `commitment_reassigned`, and `chat_dismissal` outcomes per counterparty org, with its own small-n floors baked in (≥5 captured for the group, ≥70% dismissed, cap 3) — below the floors it returns `[]` and this pass says nothing. Render each proposal as a REVIEW item on the same confirm/edit/skip widget as Pass 7b, using the proposal's `plain` line: *"You set aside 12 of the last 15 things I captured about [vendor] — want me to keep those on file without asking?"*

- **On `confirm`:** `apply_gate_proposal(workspace_root, proposal)` — ONE tap writes ONE per-org observed-only directive through the customization layer (origin `learned`), nothing else. Log the approval to `proposal_ledger` (`loop_gate_tuning`).
- **On `skip`/decline:** log the proposal's `fingerprint` (`cgd_<hash>`) to `proposal_ledger` with the standard 60-day cooldown — a declined proposal stays quiet, and the NEXT run passes those fingerprints back via `cooldown_fingerprints`.
- **Never auto-apply.** A proposal the user didn't tap changes nothing; no capture or scheduled path may call `apply_gate_proposal` (its contract says user-approval-only). Items with a due date or money still always surface regardless of any directive (the gate's asymmetric-caution rail) — say so in the proposal line when relevant.

Fires: only when `propose_gate_directives` returns ≥1 proposal; contract + spec: `shared/COMMITMENT_SCHEMA.md` § Observed tier ("Verb-driven tuning").

### Pass 8 — Classification review (batched, silent-by-default)

This is the **only user-interactive pass** in the skill. It exists because silent capture (per `shared/PASSIVE_CAPTURE.md`) deliberately never interrupts the CEO during the week — all provisional and low-confidence classifications are queued here. See `references/ORG_AND_THREAD_MODEL.md` for the full model.

**Scan window:** all events in `events.jsonl` since the last `classification_review` event (or the last 7 days, whichever is shorter).

**Select candidates:**
- `classification_confidence` in `[0.40, 0.75)` → provisional (show summary + top 2 alternative threads; one-key confirm)
- `classification_confidence` < `0.40` → low-confidence (requires explicit assignment)
- Any event where the user previously corrected a similar source/pattern (pulled from `classifier_feedback.jsonl`) → re-surface even if confidence was ≥0.75, because the classifier is newly uncertain.

Cap the review batch at **25 events** per session. If more exist, queue the excess for the next pass.

**Review UX — a widget, not a typed-reply table (P1.1/P1.5 2026-07-02; the pre-v3.13.0 "reply with row-# + action" grammar is retired — it could never render through the validator).** Render via `render_chat_output_widget()` as REVIEW items, one per candidate, posted via `widget_transport.render_and_persist` → `show_widget` (`transport["html"]` as `widget_code`) (`shared/CHAT_ACTION_WIDGET.md` § Transport). The layout below defines CONTENT only — what each item carries — never the transport:

```
Item content (per candidate):
  event summary        e.g. "Apr 18 email from Rio ('invoice batch')"
  current filing       "Filed under: Acme Restaurant — likely" (plain-English
                       confidence word per the output rules below; NEVER a decimal)
  alternatives         "Could also be: Summit Food Truck / Summit Bakery"
                       (listed in the item body as context for the edit input)
```

Actions per item (REVIEW cluster, all canonical): `confirm` (current filing is right) · `edit [change]` (the input names the correct project — replaces the old "change to <letter>") · `skip` (keep as-is, revisit later).

**On dispatch (via apply-choices on this skill's `src`):**
1. For `confirm`: no per-row event (the confirmation is captured in the aggregate `classification_review` event at the end).
2. For `edit [change]`: resolve the typed project name against the item's alternatives first, then the full project list; append a `reclassification` event referencing the original `seq`, and a `classifier_feedback.jsonl` row marking `user_action: "changed"` with signals_used, so future passes can down-weight that signal.
3. For `skip`: record `user_action: "skipped"` in classifier_feedback.
4. After the batch, append a single `classification_review` event summarizing `{reviewed_event_seqs[], confirmed_count, changed_count, skipped_count}` and trigger view regeneration.

**Confidence trend surfacing (for the insights body, not the review table):** if the last 4 weeks of `classifier_feedback.jsonl` show the user overriding a specific signal repeatedly (e.g., "email domain cluster" wrong >3 times for Acme Co), add a top-level note for the user: "I keep filing [signal] under [org/project] and you keep moving it. Want me to remember where it really belongs so I get it right next time?" This turns the feedback loop into a visible coaching signal, not a silent tune-up.

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
- Never execute a partial batch on ambiguous input. If the Apply-all payload contains any tuple that doesn't dispatch cleanly (unknown item number, an `edit [change]` whose input resolves to no project, conflicting duplicate tuples), ack: "Couldn't place: [the problem items]. Fix those and Apply again — I'll hold the batch." Do NOT emit any events for the good tuples — atomicity over partial progress.
- `skip all` (canonical bulk verb) → treat as skipping every item (append a `classification_review` with `confirmed_count: 0, changed_count: 0, skipped_count: <N>` and move on).

**E. Stale row (event reclassified by another skill between render and reply):**
- Before processing each row, re-read its event by `seq` from `events.jsonl`. If `primary_thread_id` differs from what the table showed, skip that row and include it in a "stale rows skipped" note at the bottom of the review confirmation. Do not reclassify based on stale evidence.

**F. Duplicate row instructions in one reply:**
- If the same item appears twice in the payload with conflicting actions, reject the whole batch per case D. If duplicates are identical, accept the first and ignore the rest; do not emit duplicate events.

**G. Unknown alternative letter:**
- If an `edit [change]` input names a project that resolves to nothing (not an alternative, not in the project list), reject that item per case D. Name the item's listed alternatives in the error line.

**H. Session interrupted before reply:**
- No `classification_review` event is ever emitted speculatively. If the user closes the session mid-review, nothing is persisted — the same candidates surface next run (they'll still be provisional).

**I. Alternative-project ties:**
- If two alternative projects have equal signal strength, render both as "A" and "B" in order of (a) most-recent OBSERVED activity on the project (derive via `thread_activity.derive_thread_activity(ws, honor_reclassifications=True)` — RECL1: the skill that PRODUCES reclassifications reads through them; never the deprecated `last_activity` record stamp, which no writer maintains; `first_seen` is the zero-event fallback), (b) alphabetical `display_name`. Never render three alternatives — if more exist, collapse the 3rd+ into a footnote "(+N more; say `expand row <#>` to see)."

**J. No alternatives exist (low-confidence event with no credible alt):**
- Render the item's alternatives as "Could also be: a new project / not tied to any project". An `edit [change]` input of "new project" spawns one via workspace-manager (this skill hands off, doesn't create projects itself); "no project" / "not tied to any project" files it workspace-level.

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

#### Review UX — a widget, not a typed-reply table (P1.1/P1.5 2026-07-02)

Render via `render_chat_output_widget()` as REVIEW items, one per proposal. The layout below defines CONTENT only — never the transport:

```
Item content (per proposal):
  proposed name    e.g. "ABC Supplier contract"
  signal           "recurring weekly call + 2 commitments"
  people           "Rio, Avery"
  recommendation   "Fold into: Acme Restaurant" when a merge candidate scored
                   >= 0.5, else "New project" (this is what `confirm` accepts)
```

Actions per item (all canonical): `confirm` (accept the recommendation shown — merge when a fold-into target is named, create otherwise) · `edit [change]` (override: type "new project" to force create, or name the project to fold into) · `not relevant` (decline — the fingerprint enters its 60-day cooldown) · `skip` (defer; re-surfaces next run).

#### On dispatch (via apply-choices on this skill's `src`)

For create (a `confirm` on a "New project" recommendation, or an `edit [change]` of "new project"):
1. Hand off to `workspace-manager` with payload: `{action: "create_project", display_name, kind: inferred, affiliation_id: best_guess_org, stakeholder_person_ids: involved_people}`.
2. workspace-manager creates the project record in `entities.json` and returns the new `project_id`.
3. Back in this skill: append a `project_proposal` event (`{user_action: "created", new_project_id, fingerprint}`) and a `classifier_feedback.jsonl` row with `type: project_proposal, user_action: "created"`.
4. The prior orphan events that fed the proposal are then eligible for reclassification in *next week's* Pass 8 — we don't auto-reclassify them now. (Separation of concerns: Pass 9 creates the container, Pass 8 fills it.)

For merge (a `confirm` on a "Fold into: X" recommendation, or an `edit [change]` naming a project):
1. Hand off to `workspace-manager` with payload: `{action: "reclassify_events", seqs: [the orphan event seqs that fed the proposal], new_primary_thread_id: merge_target}`.
2. workspace-manager appends `reclassification` events (one per seq). (The reclassification events themselves ARE the recency signal — activity readers derive from events; nothing updates the deprecated `last_activity` record stamp, and no writer ever has — this sentence used to make the F-61 false-writer claim.)
3. Back in this skill: append a `project_proposal` event (`{user_action: "merged", target_project_id, fingerprint}`) and a `classifier_feedback.jsonl` row.

For decline (`not relevant`):
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

**D. Malformed batch:**
Same atomicity rule as Pass 8 — if any tuple doesn't dispatch cleanly (unknown item, an `edit [change]` naming an unresolvable project), ack "Couldn't place: [items]. Fix those and Apply again." Emit no events.

**E. workspace-manager hand-off fails (for create or merge):**
Rollback: do NOT append the `project_proposal` event, do NOT append the `classifier_feedback.jsonl` row. Surface in `_hq/CONFLICTS.md` with the fingerprint + attempted action. Next run will re-propose (fingerprint never entered cooldown because action didn't commit).

**F. User forces create (an `edit [change]` of "new project") when the candidate has a strong merge target:**
Honor the user. Do not override or ask again. The learning loop via `classifier_feedback.jsonl` will note that the signal was correct (proposal fired) even though the merge suggestion was overridden — down-weights the merge-candidate scorer for similar future candidates with the same org cluster.

**G. Two proposals resolve to the same underlying entity:**
Before rendering, de-duplicate candidates whose fingerprints match (same people + same normalized name) or whose merge targets are identical. Keep the highest-scoring one; discard duplicates silently.

**H. Proposal for a person-only relationship (no org anchor):**
If a proposal is driven primarily by "high-engagement person" signals and has no org affiliation, render the recommendation line as `(person relationship — a contact record may be the right home)`. This nudges the CEO to consider whether the right container is a project or just a people-crm relationship record. The create action still works but hints at the lighter-weight alternative.

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

- **~~Daily — Pulse surfaced high-confidence proposals (score ≥10) same-day.~~ RETIRED (LIFECYCLE1) — the Pulse chat is gone and nothing replaced its same-day peek. This weekly pass is now the only proposer; a high-confidence candidate waits until Sunday, which is the cadence M asked for.** The historical action set was: Same `a/b/c confirm/edit/skip` action set as the people-record review block. Pulse reads pending org proposals from `_hq/insights/.org_proposal_queue.jsonl` + any from this run that scored ≥10. Daily surfacing makes obvious new orgs (a new client lands hard) get attention same-day rather than waiting for Sunday.
- **Weekly — insight-generator's regular Sunday run renders all remaining ≥7 candidates** in the standard 3-cap review section. Lower-confidence proposals batch here.

Same fingerprint across both surfaces — if surfaced daily and acted on, weekly review skips it (cooldown applies). If surfaced daily and ignored, weekly review will re-surface (with `(unresolved from earlier this week)` tag).

#### Review UX — render under its own section

```
## Proposed Orgs — [N]

Review: reply with row-# + action (create / edit [type] / ignore).
If you ignore one, I won't suggest it again for 60 days.

| # | Inferred name | Inferred type | Signal | Action |
|---|---------------|---------------|--------|--------|
| 1 | Acme Logistics | vendor (4 emails, 1 invoice) | 1-off purchase | — |
| 2 | Acme Co | client (12 emails, 3 meetings) | recurring + reciprocal | — |
| 3 | Northstar Partners | advisor (8 emails, 2 board references) | board language | — |
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

**F. Daily surfacing conflicted with weekly review:** MOOT since LIFECYCLE1 (there is no daily pass left), and the guard stays because the substrate still holds rows it wrote. Same fingerprint won't surface in both passes. LB2: the daily pass persisted a surfaced proposal as a `brain_proposal` row (`kind: org`/`project`, fingerprint `org:<name>`/`project:<name>` — the bp row IS the surfaced-daily mark). insight-generator skips any candidate whose fingerprint has an open bp row, a `brain_proposal_resolved`/`_expired` tombstone, or an active ledger cooldown — plus, for pre-LB2 history, any legacy event still carrying `surfaced_daily: true`.

**G. Domain looks like an alias of an existing org:** suppress with -3 penalty AND surface a top-level note: "I see [N] emails to/from [new_domain]. Looks like it might be the same as [existing_org] — want me to link them?" One-line suggestion separate from the proposal table.

**H. Proposed org would create a duplicate:** dedupe at proposal time. If two candidates (different signal sources) point to the same domain or workspace_id, merge to one proposal with combined signals.

---

### Pass 11 — Voice calibration review (monthly, interactive, B1)

Batches accumulated voice corrections into proposed voice-block updates. Modeled on Pass 10 (gating, 3-cap, fingerprint cooldown, atomic-reject, rollback). The customer-side write target is `_hq/voice/voice-block-<skill>.md` — NEVER the plugin SKILL.md (it is overwritten on update).

**Gating (all must hold):** run only when NO `voice_calibration_review` event exists in the last 28 days. If Pass 8's backlog overflow already triggered this Sunday fire, defer Pass 11 to next week. Zero candidate patterns → skip silently.

**Candidates:** read corrections via `voice_corrections.load_corrections` + `group_correction_patterns`. A pattern is a candidate when **3+ corrections share the same normalized pattern** (per skill, per `correction_type`). Cap **3 proposals per session** (Pass 9 precedent); overflow to `_hq/voice/.calibration_queue.jsonl`. Skipped-proposal fingerprints get a 60-day cooldown.

**Render (widget):** one proposal per row, actions `confirm` / `edit [text]` / `skip` (CANONICAL_ACTIONS). Each proposal names the pattern in plain English ("you've rewritten 'circle back' to 'following up on' 4 times — want me to stop using 'circle back'?").

**On confirm:** call `voice_corrections.write_voice_block_override(workspace_root, skill, <updated block>, calibration_level=…, sample_count=…)` (atomic; bumps `Last refreshed:`), then append a `voice_block_updated` event. **Malformed reply → atomic reject** (no partial write). **Mid-write failure → rollback, append NO `voice_calibration_review`.**

**Universal-pattern promotion:** when the same pattern appears across **3+ skills**, write the override into every affected skill's `voice-block-<skill>.md` AND add one ack line suggesting plugin-side promotion via `report bug` (so Chalette can fold it into `shared/VOICE_CALIBRATION.md`'s banned list for all installs).

**At end of pass:** append one `voice_calibration_review` event `{reviewed_through: {<skill>: <max correction ts reviewed>}, proposed, approved, skipped}`. "Unreviewed" for staleness = corrections with `timestamp` after `reviewed_through[skill]` — the corrections log is NEVER rewritten.

---

### Pass 13 — Sender-priority proposals (weekly, interactive, Phase 6 Loop 1)

The inbox is the highest-frequency surface in the product, and every action the CEO takes on it is the strongest triage-relevance signal it receives. `apply-choices` now captures each inbox action as a `triage_feedback` event at dispatch time (`{sender, domain, bucket_assigned, action_taken, draft_offered}`). This pass mines those to propose sender/domain priority rules where the CEO's behavior consistently contradicts the bucket the inbox orchestrator assigned. It is a generalization of the hand-coded financial-signal override (+30 for billing@) that shipped only after a $10,400 estimate was filtered out — a learned model catches the next one, for every sender class. Modeled on Pass 9/10 verbatim (3-cap, 60-day fingerprint cooldown, atomic-reject, silent-fire-queues-then-explicit-renders).

All the deterministic work is in `shared/scripts/triage_feedback.py` — this pass orchestrates it:

**Gating:** run only when NO `sender_priority_review` context is fresher than 7 days AND there are `triage_feedback` events in the 30-day window. Zero candidate rules → skip silently (no event, no empty widget).

**Candidates:** `load_triage_feedback(workspace_root, since_iso=<30d>)` → `aggregate_sender_signals(rows)` → `propose_sender_rules(agg, existing_rules=<store>.rules, cooldown_fingerprints=<cooldowns>, cap=3)`. Cooldowns come from `proposal_ledger.active_cooldowns(workspace_root, "pass13_sender_priority", now_iso=…)` (declined fingerprints, 60 days). The floors are baked into the helper: **≥4 actions** on the same sender/domain in-window and **≥80% consistency** in one direction before anything is proposed — deliberately stricter than Pass 11's 3-correction floor because a wrong demotion could bury a real high-value sender. Domain rules are preferred over member-sender rules (one proposal fixes more).

**Render (widget):** one proposal per REVIEW item via `render_chat_output_widget()`, actions `confirm` / `edit [change]` / `skip` (CANONICAL_ACTIONS). Each names the pattern in plain English (the helper's `plain` field: *"You've skipped 9 of the last 10 messages from newsletters@promo.example.com — stop surfacing them?"* / *"You act on almost everything from Rio Sample — always surface it near the top?"*). NEVER show the ±30 delta, a score, or an address as a raw token to a leak scanner — the plain string is the surface.

**On dispatch (via apply-choices on this skill's `src`):**
- `confirm` → `triage_feedback.rule_from_proposal(proposal, added_ts=<now>)`, append it to the `_hq/data/sender-priority-rules.json` store (`load_sender_priority_rules` → append → `write_sender_priority_rules`), append a `sender_priority_proposal` event `{user_action: "applied", fingerprint, sender_or_domain}`, and log the decision via `proposal_ledger.append_decision(workspace_root, pass_name="pass13_sender_priority", fingerprint=…, user_action="applied", summary=…)`.
- `edit [change]` → apply the CEO's adjustment (e.g. flip demote→promote, or narrow a domain rule to one sender) then store + `user_action: "edited"`.
- `skip` → defer (re-surfaces next run); `not relevant` / decline → `sender_priority_proposal` `{user_action: "declined", fingerprint}` + a `declined` ledger row → 60-day cooldown.
- **Malformed batch → atomic reject** (Pass 8 rule, verbatim): if any tuple doesn't dispatch cleanly, ack "Couldn't place: [items]. Fix those and Apply again." and emit NO events. **Mid-write failure → rollback**, surface in `_hq/CONFLICTS.md`, append no proposal event.

**Consumer:** the inbox orchestrator loads the store in Phase 4 scoring, AFTER the hardcoded rules + financial-signal override and BEFORE ranking, via `apply_rules_to_score(base, sender=…, domain=…, rules=…)`. The learning changes what surfaces — it NEVER auto-acts on mail.

---

### Pass 14 — Surface-preference proposals (weekly, interactive, Phase 6 Loop 2)

`chat_dismissal` (24h) and `dont_forget_feedback` (14d) are re-surfacing timers, not preferences: skip the same chase for the same person every day and it returns every day, forever. This pass turns a repeated "no" into a durable suppression — the #1 trust-eroder in the daily loop (being re-asked what you already declined). Deterministic work is in `shared/scripts/surface_preferences.py`.

**Gating:** run weekly; skip silently when no fingerprint clears the repeat floor.

**Candidates:** `load_dismissals(workspace_root, since_iso=<30d>)` reads BOTH families across all 8 widget surfaces and normalizes each to a stable `(surface, item_class, entity_id)` fingerprint (Phase-6 writers stamp `data.fingerprint`; legacy events derive it best-effort). `count_repeats(rows, min_count=3)` keeps fingerprints dismissed **3+ times in 30 days**; `propose_suppressions(counts, entity_names=<resolved names>, existing_prefs=<store>.suppressions, cooldown_fingerprints=<cooldowns>, cap=3)`. Cooldowns via `proposal_ledger.active_cooldowns(workspace_root, "pass14_surface_preferences", now_iso=…)`.

**Render (widget):** one proposal per REVIEW item, `confirm` / `edit [change]` / `skip`. Plain-English (`plain` field): *"You've skipped chasing Dana 6 times — never suggest chasing them?"* / *"You've dismissed 'Project Atlas is going stale' 4 weeks running — stop flagging it?"*. Declines get their own 60-day cooldown so the pass doesn't nag about the nag.

**On dispatch:** `confirm`/`edit` → `suppression_from_proposal(...)` appended to `_hq/data/surface-preferences.json` + a `surface_preference_proposal` `{user_action, fingerprint}` event + `proposal_ledger` row. Decline → cooldown. Same atomic-reject + rollback rules as Pass 13.

**Consumer:** EVERY widget orchestrator (inbox, commitments, past-meetings, upcoming-meetings, friday-wrap, relationship-moves, morning-brief, staff-meeting) calls `is_suppressed(prefs, surface, item_class, entity_id)` to filter items BEFORE rendering — see each orchestrator's pre-render step. A suppression only hides a surfaced prompt; it never changes what's captured in the substrate.

---

### Global proposal cap (Phase 6 — passes 8–16 share one weekly widget)

Passes 9, 10, 11, 13, 14 (and later 15, 16) each keep their own 3-cap. On top of that, the weekly review honors ONE global ceiling so a busy week doesn't bury the CEO under a dozen prompts at once. `proposal_ledger.GLOBAL_PROPOSAL_CAP` (7) is that ceiling. Render the proposing passes in priority order — highest-leverage daily-surface passes first (13 sender-priority, 14 surface-preferences), then 9/10/11, then the Round-2 calibration passes below — and before rendering each pass call `proposal_ledger.remaining_global_slots(rendered_so_far)`; a pass renders at most `min(3, remaining)` proposals and queues the rest to its own `.*_queue.jsonl` for next week. The CEO sees a coherent short review, not five stacked widgets.

---

### Loop 4 — Confidence calibration (monthly, interactive, Phase 6 Round 2)

The CRU match-score thresholds (`MATCH_SCORE_AUTO_RESOLVE` = 0.55, `MATCH_SCORE_PENDING_REVIEW` = 0.30) are one-size-fits-all constants, but every workspace records how accurate its own bands are: a `commitment_review_proposed` carries the `match_score`, and the CEO's later `resolved` / `not relevant` says whether that band was right. This pass tunes the dial per workspace. Deterministic work is in `shared/scripts/confidence_calibration.py`.

**Cadence + discipline:** monthly (or on the weekly fire, gated to once/28-days). `load_review_outcomes(workspace_root)` joins proposals to their terminal outcome; `propose_calibration(outcomes, current_auto_resolve=confidence.match_score_auto_resolve(workspace_root), cooldown_fingerprints=…)`. **ONE proposal max per run** (Loop 4 spec), small-n floor **≥20 terminal outcomes in a band**. Two directions: LOOSEN (a pending sub-band confirmed ≥95% → lower auto-resolve to that floor) or TIGHTEN (auto-resolves getting reopened above ~10% → raise it).

**Render:** one REVIEW item, `confirm`/`edit [change]`/`skip`, plain-English (*"You've confirmed 96% of the 22 'likely' matches I flagged for review — want me to just auto-close that strong a match instead of asking?"*). NEVER show a decimal threshold to the CEO. On `confirm`, `apply_calibration(workspace_root, proposal)` merges the value into `_hq/data/confidence-overrides.json` (read by `confidence.py` accessors → cru_match), append a `confidence_override_proposal` event, log to `proposal_ledger` (`loop4_confidence_calibration`). Decline → 60-day cooldown. Atomic-reject + rollback as Pass 9. The override only moves auto-resolution precision — it never changes what's captured.

---

### S3 rider — Commitment noise thresholds (weekly, interactive, Phase 6 Round 2)

The Stage-D capture floor (clear owner + deliverable + consequence) that cut one workspace's open set 71→33 is a hardcoded global rule. This rider makes noise thresholds learnable: when a counterparty's captured commitments are mostly noise (repeatedly resolved `dropped`), propose a `never-track` rule the CEO approves — appended to `_hq/config/commitment-rules.md`, the SAME file the capture floor reads (and the `never track this` triage action already writes). Deterministic work is in `shared/scripts/commitment_noise.py`.

`analyze_noise(workspace_root)` → per-counterparty drop stats; `propose_noise_rules(stats, existing_rules=commitment_noise.load_never_track_rules(workspace_root), cooldown_fingerprints=…, cap=3)` with floors **≥8 resolved commitments from a source and ≥50% dropped**. Render one REVIEW item per proposal (*"You've dropped 6 of the last 8 things I captured about Sample Vendor — want me to stop tracking low-stakes items from them?"*). On `confirm`, `append_never_track_rule(workspace_root, proposal["pattern"])` (additive; deduped), append a `commitment_noise_proposal` event, log to `proposal_ledger` (`s3_commitment_noise`). Decline → 60-day cooldown. This rides the same propose-approve machinery — never a silent capture change; every commitment producer picks up the new rule at its next capture.

---

### Pass 15 — Prep-brief section weights (monthly, interactive, Phase 6 Loop 3)

call-prep writes a brief before a meeting; past-meetings grades it against the transcript afterward (`prep_feedback` events — see orchestrator-past-meetings). This pass turns that grading into sharper briefs: aggregate `prep_feedback` per meeting-type and propose dropping a section that's consistently rendered-but-empty. Deterministic work is in `shared/scripts/prep_grading.py`.

**Monthly.** `load_prep_feedback(workspace_root, since_iso=<window>)` → `aggregate_section_stats(rows)` → `propose_section_weights(stats, existing_weights=<call-prep config>.section_weights, cooldown_fingerprints=…, cap=3)`. Floor: **≥6 meetings of that type** and the section empty **≥80%** of them. Render one REVIEW item per proposal (*"The Risks section came up empty in 8 of your last 9 internal 1:1s — drop it for those?"*), `confirm`/`edit [change]`/`skip`. On `confirm`, `set_section_weight(config, meeting_type, section, 0)` then `skill_config_writer.save_skill_config(workspace_root, "call-prep", config, is_reconfigure=True)`; append a `prep_weight_proposal` event; log to `proposal_ledger` (`pass15_prep_grading`). Decline → 60-day cooldown. call-prep reads `prep_grading.section_weight(config, meeting_type, section)` before rendering — a weight of 0 drops that section for that meeting-type.

**Output-profile proposals (SPEC OUT2 §5 — a sanctioned write target, same confirm-first shape).** When the review passes surface a consistent cross-skill document pattern (e.g. the CEO repeatedly asks for shorter documents, or repeatedly deletes tile bands in corrections), this pass MAY additionally propose ONE output-profile change — density / visual bias / a page cap for one kind — rendered as a REVIEW item with `confirm`/`edit [change]`/`skip`, never applied silently. On `confirm`: validate via `output_profile.validate_output_profile`, then `skill_config_writer.save_skill_config(workspace_root, "output_profile", profile, is_reconfigure=True)`; decline → 60-day cooldown via `proposal_ledger`. This and the explicit "tune output" verb (workspace-manager) are the ONLY writers of `_hq/data/skill_config/output_profile.json` — no first-run block, no onboarding mention, ever (the OUT2 §5 fence).

---

### Loop 5 — Extraction-miss learning (monthly, interactive, Phase 6 Round 3)

The substrate's front door improves from its own documented failures. Two miss classes plus the session-sweep's recoveries are collected, clustered, and — on approval — written as few-shot exemplars that meeting-notes and cru_match read. Deterministic work is in `shared/scripts/extraction_hints.py`.

**Capture (writers tag; see the consumer skills):** decision-log tags a manually-logged decision `data.extraction_miss=True` when `extraction_hints.find_recent_meeting(new_event, meeting_events)` finds a processed meeting within 24h sharing an attendee (the manual commitment-log path uses the same helper); the inbound CRU leg (commitments orchestrator) marks `data.resolution_miss=True` when `is_resolution_miss(reply)` fires on a reply that carried NO CRU match — that leg already reads the reply body, so it is the privacy-correct home (reconcile-sent's outcome watch stays metadata-only); and the Phase-5 session-sweep's recoveries (`source_ref = "session:<id>"`) that overlap a processed meeting are consumed as extraction-miss signal too.

**Monthly.** `load_misses(workspace_root, since_iso=<window>)` (all three sources) → `cluster_misses(rows, min_cluster=3)` → `propose_hints(clusters, existing_hints=extraction_hints.load_extraction_hints(workspace_root), cooldown_fingerprints=…, cap=3)`. Render one REVIEW item per proposal (*"I've missed 4 similar items you had to log by hand — want me to learn to catch that phrasing?"*), `confirm`/`edit [change]`/`skip`. On `confirm`, `append_extraction_hint(workspace_root, proposal["hint"])` (additive; deduped), append an `extraction_hint_proposal` event, log to `proposal_ledger` (`loop5_extraction_hints`). Decline → 60-day cooldown. Consumers: meeting-notes reads the hints at extraction time; cru_match reads them for resolution language.

---

### Pass 16 — Exemplar structure review (weekly, interactive, SPEC OUT8)

Voice calibration (Pass 11) learns WORDS from corrections; this pass learns STRUCTURE the same way. Composers capture structural corrections — the user reorders, drops, or reshapes a delivered document — to `_hq/exemplars/corrections-<kind>.jsonl` (via `exemplars.append_structural_correction`; reconcile-sent's sent-doc diff and the composers' "make it like this" feedback are the two capture sites). When a pattern repeats, this pass proposes updating that kind's workspace exemplar — the structural gold standard every composer anchors on (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor"). Deterministic work is in `shared/scripts/exemplars.py`.

**Weekly.** `load_structural_corrections(workspace_root)` → `propose_exemplar_updates(rows, cooldown_fingerprints=proposal_ledger.active_cooldowns(workspace_root, "pass16_exemplar_structure", now_iso=…), cap=3)`. Floor: **≥3 same-direction corrections on one kind** (same kind + direction + section). Render one REVIEW item per proposal — the helper's `plain` line only (*"You've moved the KPI table above the narrative in 3 recent board pack documents — make that the standard layout?"*), `confirm`/`edit [change]`/`skip`. On `confirm`, build the amended skeleton — PREFER the current exemplar with the confirmed change applied (it is already synthetic); when borrowing from the delivered doc, take STRUCTURE only and replace every name, figure, and claim with placeholders YOURSELF before promoting (the scrub gate only knows the workspace entity list — an untracked counterparty name or deal figure is yours to strip). Then run `exemplars.residual_name_candidates(new_text)` and put BOTH lists on the confirm card: the scrub replacements and the residual name-shaped tokens the entity list cannot vouch for; anything the user identifies as real gets replaced with a placeholder, never confirmed through. Write via `exemplars.promote_workspace_exemplar(workspace_root, kind, new_text, confirmed_residuals=<the user-confirmed list>)` — the scrub gate replaces entity names with placeholders, re-runs the leak scan, and refuses on any unconfirmed residual candidate; a refusal is surfaced honestly, never bypassed. The previous exemplar rotates to `exemplar_2.md`. Append an `exemplar_update_proposal` event (`{user_action, fingerprint, kind}`), log to `proposal_ledger` (`pass16_exemplar_structure`). Decline → 60-day cooldown; skip → soft defer, no cooldown. Atomic-reject + rollback as Pass 9.

⛔ **Never a silent write:** shipped seeds under the plugin's `shared/exemplars/` are NEVER touched from a workspace; the ONLY exemplar writer is `promote_workspace_exemplar` after an explicit user confirm on this pass's widget (or the user asking for the change in so many words). Deleting `_hq/exemplars/<kind>/` is the reset — clean fallback to the shipped seed.

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

**That call is the only generator (DOCFENCE1):**

- **NEVER hand-roll the insights doc** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or PII-leaking document (the v3.20.0 failure mode) — and this doc is dense with people, customers, and relationship read-outs, which is precisely what the leak scan is for.
- **NEVER create, render, copy, upload, or update the insights doc — or any part, derivative, or restatement of it ("the top three", "a summary") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `_hq/insights/` (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the insights in a Google Doc" is a request this gate refuses, not an override. The inline top-3-to-5 in chat is the read-without-opening surface; the file is the deliverable.

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

**Silent-fire rule (scheduled runs queue; explicit runs render).** The Sunday scheduled fire is SILENT: it computes and QUEUES Pass 8/9/10 candidates (`_hq/insights/.review_queue.jsonl` + the proposal fingerprints) and writes the .docx — it never renders interactive widgets into a chat nobody is looking at. The widgets render on the next EXPLICIT-trigger run ("weekly insights" / "review classifications" / workspace-manager's are-they-ready offer), reading the queue first.

**Run receipt (v4.5.2 R1 — REQUIRED, every run, scheduled or explicit).** weekly-insights was the ONE scheduled task with no substrate receipt — the health watchdog had to fall back to view-file mtimes and run counts were impossible (FINDINGS F-49's missing-row class). Final step of every run, one line via the canonical helper: `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "weekly-insights", fired_via="scheduled", extra_data={"views_written": [...], "passes_run": [...], "candidates_queued": n})` — `"manual"` for fired_via on explicit-trigger runs. A skip-not-fail run (fresh workspace floor) still writes the receipt with `extra_data={"skipped": "<reason>"}` — a silent skip must be distinguishable from a silent failure.

**Pass 8 (classification review) and Pass 9 (project proposals) — separate widgets, not embedded in the .docx body.** Pre-v3.13.0 the classification review and project proposals were rendered as tables inside the insight report. v3.13.0+: surface those as separate widgets per the standard chat-action-widget contract (with `confirm` / `edit [text]` / `skip` actions per CANONICAL_ACTIONS) so the user can act on them directly. The .docx itself contains observations + suggestions, not interactive surfaces.

**Forbidden in the .docx body and the chat surface (per universal voice contract):**

- ❌ "Window: 2026-05-13 to 2026-05-20 · Events analyzed: 247" — surface this as "Looking back at this week" in prose, not as metadata
- ❌ "Confidence: 0.87 · Signals: 3" — drop the score; if signal strength matters, frame in prose ("strong signal" / "worth a look but I'm not sure")
- ❌ "person_072 surfaced in 4 events with project_020" — use names ("Aria came up in 4 conversations about Northstar")
- ❌ Section headers like "STABLE / ROUTINE" — use sentence case ("What's on track")
- ❌ "Classification Review — N items" as a doc section header — split off into a separate interactive widget
- ❌ Any reference to `.json` / `.jsonl` / `_hq/` paths in the user-facing prose

---

## Skill Boundary (v2.1)

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

The weekly-insights pass runs as a JOB inside the `maintenance` task (MAINT1), which is registered by **enable-command-room-schedules Step 1.D** and back-filled by **command-room-update-bridge**. Do not register anything from this skill; cadence changes go through **change-schedule** (moving `maintenance` moves all its slots; `pause weekly insights` pauses just this job).

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

- Does not write to entities.json directly — with the ONE declared Writer Contract exception: the `dormancy_reviewed_at` field Pass 7 stamps on dormant project records (atomic, cooldown-gating only). All other entities.json mutation is delegated (workspace-manager for Pass 9, `org_writer` for Pass 10).
- Interactive writes (Pass 8 reclassifications, Pass 9/10 proposals, Pass 11 voice blocks) require explicit user action on the review widget — never silent. The declared silent writers are exactly two: Pass 7's passive-capture events and the projection/view refresh. The full write inventory lives in the Writer Contract above — this section defers to it.
- Does not mutate prior events. Reclassification = new append with `supersedes_seq`, per schema.
- Does not propose projects on a fresh workspace. Pass 9 inherits the ≥14-day minimum from the skill's overall gate; Pass 9 itself requires ≥30 days of data before proposing, since cadence signals need time to materialize.
- Does not answer one-off questions ("why is NorthStar stuck?") — that's `workspace-manager` with connector context.
- Does not produce generic advice — only workspace-grounded observations. If the workspace has <14 days of events, the skill exits cleanly without producing an insight report.
- Does not send or act on insights — every insight is a prompt for CEO attention, never an auto-action.
- Does not interrupt mid-week to ask about classifications — that's the whole point of Pass 8 being batched and weekly.
- Does not replace the cleanup — audit checks workspace health; insight-generator synthesizes patterns.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Weekly synthesis pass that surfaces patterns the CEO wouldn't have seen. Fires on: 'weekly insights', 'what am I missing', 'run insights', 'synthesize the week', 'what should I pay attention to', 'cross-project patterns', 'what's drifting', 'show me the insight report', 'generate insights', 'classification review', 'review classifications', 'review project proposals', 'new project proposals'. DOES NOT fire on 'review my projects' (list-active — a roster status read, not the classification pass) or 'what should I focus on' (command-room-coach — forward-looking priorities; this skill surfaces backward-looking patterns). Also runs automatically on a weekly schedule (Sunday evening by default) via scheduled-tasks. Reads generated views (TIMELINE, RELATIONSHIPS, COMMITMENT_AGING, DORMANT, THEMES) + entities.json + events.jsonl and produces (a) a ranked list of observations worth the CEO's attention, (b) a batched classification review pass covering every provisional / low-confidence event from the prior week (Pass 8), (c) a batched project-proposal pass surfacing new projects when accumulated signal suggests outcomes will slip without a dedicated anchor (Pass 9), and (d) a batched org-proposal pass surfacing new orgs when capture events accumulate enough signal without an explicit attribution (Pass 10). Saves the synthesis to _hq/insights/[YYYY-MM-DD]_insights.docx (v3.13.0+) and surfaces a link at the bottom of the chat turn. Pass 8 appends `classification_review` + `reclassification` events and `classifier_feedback.jsonl` rows. Pass 9 appends `project_proposal` events + `classifier_feedback.jsonl` rows and delegates entities.json mutations to workspace-manager. Pass 10 appends `org_proposal` events + `classifier_feedback.jsonl` rows of `type: org_proposal`. Phase 6 adds Pass 13 (sender-priority proposals from `triage_feedback` → `_hq/data/sender-priority-rules.json`) and Pass 14 (surface-preference/dismissal-suppression proposals → `_hq/data/surface-preferences.json`); both reuse the Pass 9/10 machinery (3-cap, 60-day cooldown, atomic-reject) and log decisions to `_hq/data/proposal_feedback.jsonl`. Does NOT handle one-off questions — use workspace-manager for that.
