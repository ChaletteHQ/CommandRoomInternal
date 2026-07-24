# Orchestrator prompt — Pulse (taskId: pulse)

**File-to-task mapping:** This file (`orchestrator-dont-forget.md`) is the EXACT prompt registered with `create_scheduled_task` for `taskId: "pulse"` (display name: "Pulse"). The orchestrator filename stays as `orchestrator-dont-forget.md` for `events.jsonl` `source_skill` back-compat — historical events written with `source_skill='cr-dont-forget'` remain valid as append-only history. New events post-v2.14.27 use `source_skill='pulse'`.

There is NO `orchestrator-pulse.md` file in this directory. If you find a registered task with `taskId: "cr-dont-forget"`, `cr-pulse`, or any other `cr-*`-prefixed legacy variant, that's pre-v2.14.27 state — disable it per the migration table in `enable-command-room-schedules/SKILL.md` and register `pulse` fresh, using THIS file as the prompt body.

Fires 9:00 AM weekdays local. Replaces the v2.7-v2.10.1 `cr-cracks-watch` task (renamed for executive clarity).

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. The renderer enforces canonical action labels (`CanonicalActionError`) and blocks leaks (`LeakDetectedError`) before any post. Rules 1–18 are non-negotiable. The widget + Links section is the ENTIRE chat turn; STOP after that. No commentary, no narration.
**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md` for legacy markdown rules; follow `shared/CONTRACT.md` for v2.13.0 strict contract.
**Email-draft mechanics (re-engagement drafts):** follow `shared/EMAIL_DRAFT_PROTOCOL.md`. Zapier scope HARD-LIMITED to email send/reply.

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

Read `shared/STOP_CONTRACT.md` from disk and obey it as your first action of every fire. It carries the canonical post-widget output rules. Pre-v3.5.0 each orchestrator inlined a ~25-line copy; v3.5.0+ they reference the shared file.

Pulse-specific scope notes:
- **The directories `_hq/scheduled_outputs/` and `_hq/insights/<date>/` do not exist in the spec for this orchestrator.** Do NOT create them. Do NOT save `pulse.json`, `pulse.md`, `pulse_data_view.json`, or `pulse_widget.html`. The legitimate Pulse outputs are events.jsonl appends (per Phase 7 memory updates) and the `show_widget` post.
- Re-runs (`regenerate pulse`, `re-fire pulse`, `ignore prior surfacing`) re-execute Phase 1 onward; do NOT switch to file-write mode.

---

You are firing the Command Room "Pulse" chat. Two purposes, one chat:

1. **Surface cracks** — dormant relationships, cadence breaks, stale active projects (the original `cracks-watch` purpose).
2. **People-layer synthesis pass** — for everyone touched in the last 7 days of events.jsonl, re-derive their canonical record from accumulated signal. High-confidence updates auto-apply; low-confidence go to a review sub-section in the chat output. (NEW in v2.10.2 — folded in here so Pulse does double duty: dormancy detection AND person-record refresh, same connector reads, two outputs.)

# Phase 1 — Always run (no idempotency gate, v2.10.5+)

The v2.7-v2.10.4 idempotency gate was removed in v2.10.5. This orchestrator ALWAYS runs when fired — whether by cron or by manual `re-run` trigger. Multiple fires per day are intentionally allowed.

A `pack_run` event still writes at the end of every fire (for audit trail), but no gate blocks subsequent fires. Re-running surfaces the same dormancy + people + entity-proposal items unless they've been acted on between fires.

# Phase 2 — Setup

- Today's date.
- Read entities.json (people, orgs, projects), aliases.json, events.jsonl.
- M's `person_id` from entities.json.
- **Resolve the calendar tools through the seam** for the `schedule catchup [when]` handler — `tool_discovery.discover_for_category("calendar", "<op>", tools, declared=connector_config.declared_backend("calendar"))` for the event-create / event-find operations, falling back to `discover_calendar_tool(tools, "<op>")` when no backend is declared (empty map = today's behavior, R4). Native calendar via the seam, Zapier-excluded — per `EMAIL_DRAFT_PROTOCOL.md` §3c HARD SCOPE Zapier never handles calendar (the seam excludes Zapier legs automatically). If no native calendar tool resolves, `schedule catchup` degrades to email-only (drafts the request without creating a tentative invite) and surfaces a one-time per-session note: `(Calendar invite couldn't be created — native Calendar MCP not connected. Email draft staged anyway.)` Never name a provider tool id directly.
- Resolve the mail search + draft + send tools for re-engagement / status-check / catchup-request drafts via the seam (`discover_for_category("email", "<op>", tools, declared=connector_config.declared_backend("email"))` → `discover_mail_search_tool` / `discover_mail_draft_tool` fallback; send dispatch per EMAIL_DRAFT_PROTOCOL §0.5/§3c). Never name a provider tool directly. On drift (declared backend NOT PRESENT) in a scheduled fire: skip-and-flag per SHARED_CHAT_OUTPUT_PROTOCOL § Connector drift (R13) — never prompt from a silent fire.
- Discover Zapier-threaded-send tool per `EMAIL_DRAFT_PROTOCOL.md` §3c (limit to tools whose name OR description contains `Send Threaded Email`; never any other Zapier tool). Cached for the session. If none, fall back to native Gmail at send time.

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire — a typed trigger, a Run Now click, a re-run request in an open chat. **When uncertain, it is `manual`**: a mis-labeled manual costs one missing lateness note; a mis-labeled scheduled fabricates lateness history (FINDINGS F-47 P1a — three false late_fire receipts in one afternoon).

Cowork fires a missed slot at next app launch, hours or days late, and without this check the run would render a stale surface as if it were fresh. Compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local, the clock cron actually evaluates in), passing the detected run mode:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'pulse', fired_via='<scheduled|manual>')))
"
```

Branch on `tier` (this does not weaken the anti-improvisation contract — every phase below still executes verbatim; the tier only governs what is RENDERED):

- **`manual`** — an interactive fire is never late: run EVERY phase normally (connector pre-scans included — a run mode never adds skip conditions), with NO timing banner and NO lateness narrative of any kind, anywhere. The helper wrote no event; do not hand-compute lateness around it (FINDINGS F-47 P1a).
- **`none` / `exempt` / `unknown`** — run normally. No mention of timing anywhere. `none` with a `suppressed` reason means the helper's ledger found the slot already served (a receipt exists after it) or minted by a schedule change — believe it: never re-derive lateness, never invent a cause ("the computer was probably asleep").
- **`note` (3–24h late)** — run ALL phases normally, but the chat output OPENS with the returned `banner` line verbatim (one line, before anything else). Nothing else changes.
- **`degrade` (>24h late)** — the surface is stale; do NOT render it. Execute every phase below EXCEPT the surface-rendering one (the widget-render/post phase): all substrate writes the task owes — events, view updates, the Phase-final `pack_run` receipt — still happen, silently and explicitly (skipping them is the Bug #98 class: an invisible write must not lose to a suppressed deliverable). Then post ONLY the returned `degrade_notice` line as the entire chat output and STOP. No widget, no digest, no Links section. The next Morning Brief reads events.jsonl, so nothing captured is lost.

The helper already appended the `late_fire` telemetry on note/degrade tiers (cleanup and the insight pass consume it to propose better default times) — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

# Phase 3 — Per-person dormancy scan

**BAL1 D1.1(1) — personal-tie source gate (runs FIRST, before anything else in this loop):** skip every person whose record carries `tie: "personal"` BEFORE any computation or emit — no `pattern_break_detected`, no `emit_dormancy_signal`, no live-check, nothing. A spouse or parent going "quiet" is not a work signal; the moment a personal tie's dormancy enters the substrate it flows into relationship-moves and the weekly WORK-outreach pack. Personal ties belong to the Balance surface (`skills/balance/SKILL.md`) exclusively. An absent `tie` field means work (back-compat) — only the explicit `personal` value skips.

For each person in entities.json (excluding M, excluding `tie: "personal"` per the gate above, excluding flagged-orphan / left-company):

1. Compute `last_interaction_date` = max(ts) across **all events.jsonl entries that reference this person**, not just `type: "interaction"` events. **v3.13.0+ — also live-check Gmail before flagging dormancy** (see Step 1b below). Substrate-only dormancy produces false-positives when M has just emailed someone but the substrate hasn't caught up.

   ### Step 1b — Live Gmail + Calendar lookup BEFORE emitting `pattern_break_detected` (v3.13.0+ Gmail half; v3.13.7+ Calendar half + canonical helper)

   **MUST-language enforcement gate (v3.13.7+):** for every dormancy / pattern-break / cadence-gap candidate, you MUST call the shared `live_contact_check.live_contact_check()` helper from `shared/scripts/live_contact_check.py` BEFORE emitting any `pattern_break_detected` flag. The substrate-only path is NOT acceptable. The Gmail-only path (v3.13.0 partial implementation) is NOT acceptable. Both Gmail AND Calendar signals must be consulted via the canonical helper.

   This collapses Bug #5 (Pulse missing Calendar half) and Bug #28 (dormant-customer-scan missing all live signals) into ONE shared call site. Every consumer goes through the same code path; the helper handles tool discovery + the max-merge math.

   **Why this step exists:** the 2026-05-20 Cowork handoff #27 verified that Pulse computed `last_interaction_date` from events.jsonl alone, with no live connector read at fire time. The user emailed two contacts within the last 48 hours, but those sent messages never entered the substrate (inbox-triage hadn't run yet that day), so cadence math saw 6-day and 12-day gaps and flagged both as dormant. Pulse was arithmetically correct on stale data; the user experience was "you're telling me to nudge people I just talked to." v3.13.7 Session-22 testing also surfaced the parallel Calendar gap: meeting-heavy contacts (board members, recurring 1:1s, executive coaching) whose contact cadence was calendar-based hit the same false-dormancy class because the v3.13.0 Step 1b only queried Gmail.

   **The fix:** before emitting `pattern_break_detected` for any person, invoke `live_contact_check`. It returns a merged `last_contact_iso` that respects substrate + Gmail + Calendar. If the merged value is more recent than the substrate-only date, the person is NOT actually dormant — skip the flag.

   **REL1 — emit the normalized dormancy signal alongside `pattern_break_detected`.** When you emit `pattern_break_detected` for a person (after this live-check gate), ALSO call `shared/scripts/dormancy.py::emit_dormancy_signal(workspace_root, entity_id=<person_id>, entity_type='person', gap_days=<days_since>, baseline_days=<typical_cadence or None>, source_skill='pulse')`. `pattern_break_detected` is unchanged (header math + operator-report counters depend on it); the signal is additive.

   ```bash
   SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
   PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
   cd "$PLUGIN_ROOT"
   python3 -c "
   import sys
   sys.path.insert(0, 'shared/scripts')
   from live_contact_check import discover_live_check_tools, live_contact_check
   # Step A — once per fire, resolve the Gmail + Calendar tool IDs
   lookup = discover_live_check_tools(available_tools)
   # Step B — for each candidate, after invoking the discovered tools through MCP
   #          and capturing the latest Gmail thread + Calendar event ISO date
   result = live_contact_check(
       workspace_root='\$WORKSPACE',
       person_id='person_NNN',
       external_signals={
           'gmail_last_iso':       gmail_iso_or_none,
           'gmail_detail':         {'subject': '...', 'thread_url': '...'},
           'gmail_failed_reason':  None,
           'calendar_last_iso':    calendar_iso_or_none,
           'calendar_detail':      {'title': '...', 'event_url': '...'},
           'calendar_failed_reason': None,
       },
       window_days=7,
   )
   # result['source'] in ('substrate', 'gmail', 'calendar', 'none')
   # result['last_contact_iso'] is the merged max; use this instead of substrate alone.
   "
   ```

   **Step A — once per fire.** Call `discover_live_check_tools()` to resolve the Gmail-search and Calendar-find tool IDs. Tool registry is stable for a session; cache the result.

   **Step B — per candidate.** Invoke the resolved tools through MCP to fetch the latest mail thread with the candidate (the **to/from-this-address within the last 7 days** intent — a DISJUNCTION: `{"any_of": [{"from_me": true, "to": "<addr>"}, {"from": "<addr>"}], "newer_than": "7d"}`, compiled per provider by `connector_adapters/mail.py` `compile_search`; on Gmail this reproduces the original sent-to-or-received-from OR-group query) AND the latest Calendar event with the candidate's email as attendee in the last 7 days. Pass both timestamps to `live_contact_check.live_contact_check()`. Use the returned `last_contact_iso` as the actual `last_interaction_date` for the cadence math below.

   **If a tool was not discovered or its lookup failed:** pass the `*_failed_reason` field and the helper records it in `sources_failed`. Render plain-English in the chat surface ("(Calendar lookup skipped — connector not connected)") so the user knows the flag is partial-signal rather than silently treating absence as evidence-of-absence.

   **Performance gate:** ~2 connector reads per dormancy candidate (Gmail + Calendar). With 60-80 people in the workspace and typical 5-10 dormancy candidates per fire, that's ≤20 lookups per fire — well under any per-fire budget. If a fire surfaces >20 candidates (rare), batch Gmail by listing all candidates' emails once via a single search with multiple `to:` ORs; same for Calendar with multi-attendee filters.

   **Same-day backfill (v3.13.0+ — complementary fix):** inbox-triage and weekly-recap now write `interaction` / `outreach_sent` events to events.jsonl same-day for sent/received mail. Over time, this keeps the substrate fresh enough that the live-check above rarely changes outcomes. Until inbox-triage has caught up on a given day's mail, the live-check above is the safety net.

   **What to do when the live check overrides:** log a `pulse_live_check_overrode` event with `{person_id, substrate_last_interaction, live_last_interaction, source: "gmail" | "calendar"}`. This makes the override visible in audits and helps debug when the live-check fires false-positives of its own. Use the `source` field from the helper's return dict verbatim — that's the canonical attribution.

   **Reference detection (v3.5.0+ — canonical via `cru_match.event_references_person(ev, person_id)`):**

   Use the helper. It traverses every known field where a `person_id` can appear, across all 5 commitment-shape variants + meeting attendees + actor + root-level person_ids. The canonical field list lives at `cru_match._PERSON_ID_FIELDS`.

   Pre-v3.5.0 this phase inlined a 6-field list (`person_ids` / `data.person_ids` / `data.owner_id` / `data.requester_id` / `data.attendees` / `actor`). The 2026-05-17 audit found the inline list silently missed shape variants — specifically `data.owner_person_id` and `data.requester_person_id` (the variant cr-past-meetings actively produces), top-level `owner_id` (flat-new shape from Sam's bug report), and the legacy top-level `owner` field. People whose involvement was captured ONLY in those shapes were under-counted in cadence detection → mis-flagged as dormant. The helper closes that gap by being the single source of truth for "does this event reference this person."

   **Eligible event types (counted toward cadence):**
   - `interaction` — explicit interactions
   - `meeting` — meetings the person attended
   - `decision` — decisions where the person was involved (root-level `person_ids` set)
   - `commitment` — commitments made by or owed to the person
   - `outreach_sent` — emails sent to the person
   - `draft_created` — drafts written for the person
   - `follow_up_draft` — follow-ups staged for the person
   - `meeting_processed` — meetings logged with the person as attendee

   **Excluded event types (do NOT count — internal mechanics):**
   - `pattern_break_detected`, `dont_forget_run`, `dont_forget_feedback`, `dont_forget_snooze`, `cracks_watch_*`
   - `dormancy_signal`, `relationship_move_suggested` (REL1 — internal relationship-mechanics signals; never count as interactions)
   - `balance_nudge_suggested` (BAL1 D3 — the personal-lane nudge; never counts as an interaction, and no org-facing surface reads it at all)
   - `pack_run`, `connector_read`, `scheduled_task_failure`, `errors`
   - `person_record_update`, `person_record_update_rejected`
   - `tier_change`, `tier_validated`, `org_proposal_*`
   - `chat_dismissal`, `thread_resolved` (these are administrative, not interactions)
   - `degraded_baseline`, `classification_*`
   - `intro_followup_check`, `intro_landed`, `intro_didnt_land` (Phase 4h scheduled markers — counted by intro-broker's own surface, not cadence)

   **Bug context (Apr 29):** the prior cadence query only counted `type: "interaction"` and `type: "meeting"` events, missing commitment/decision/follow-up-draft/meeting-processed events that reference the person. This caused person_005 (Sam Sample) to rank as "38 days dormant" despite 5+ recent events between Apr 27–29. The eligible-type list above closes that gap.

2. Compute `typical_cadence_days`:
   - If <3 historical interactions → use absolute thresholds (>14d silence flags).
   - If ≥3 → mean inter-interaction days from last 12 events. (Read from people-crm's existing dormancy logic — don't re-derive.)
   - **Phase 6 Quick Win B — floor with the user-taught baseline.** If the person record carries `cadence_override_days` (written when the CEO previously cleared this person's "going quiet" flag as "just busy"), widen the computed cadence with it: `typical_cadence_days = dormancy.effective_baseline(computed, dormancy.cadence_override_days(person_record))`. This is why "just busy" stops the flag from simply returning every 14 days — the model of the relationship actually moved. Legacy records without the field read as no override (`effective_baseline` is None-safe).
3. `days_since = today - last_interaction_date`.
4. Flag as **dormant** if `days_since > typical_cadence × 2.5`.
5. Flag as **pattern-break** (low signal) if `days_since > typical_cadence × 1.8` (between 1σ and 2σ).
6. Skip persons with `dont_forget_feedback` (or legacy `cracks_watch_feedback`) event in last 14d marking "expected" or "ignore (just busy)".
7. Skip persons with `dont_forget_snooze` (or legacy `cracks_watch_snooze`) event currently active.

**Diagnostic logging (v2.10.9+):** when emitting the `pattern_break_detected` event for a person, include `data.eligible_event_count` (count of all events.jsonl entries matching the eligible-type list above for this person) and `data.most_recent_eligible_event_seq` (the seq of the event used as `last_interaction_date`). This makes the bug above easy to verify on future runs — if `eligible_event_count` is suspiciously low for an active person, the reference-detection logic isn't matching all event shapes and needs re-checking.

# Phase 4 — Project lifecycle pass (stale-active + dormant transitions, v2.10.3)

**The one derivation (v4.5.2 C3 — FINDINGS F-54):** compute `last_event_date` for EVERY sub-phase below via the canonical helper, once per fire:

```python
from thread_activity import derive_thread_activity
from stall_detector import DEFAULT_CONFIG
from skill_config_writer import load_skill_config

saved = load_skill_config(WORKSPACE_ROOT, "stalled-projects")
types = ((saved or {}).get("config") or {}).get("activity_event_types") \
        or DEFAULT_CONFIG["activity_event_types"]
last_event = derive_thread_activity(WORKSPACE_ROOT, activity_types=types,
                                    honor_reclassifications=True)  # RECL1
# last_event[project.id].ts → last_event_date (no entry = no activity events)
```

This is the SAME helper + SAME activity-type set + SAME `honor_reclassifications=True` the on-demand `stalled projects` skill uses, so the day-count Pulse quotes and the day-count stalled-projects quotes can never disagree for the same project on the same day (F-54's cross-surface split: pulse said 21d stale, stalled-projects said 37d, same project, same morning). It also credits `related_thread_ids[]` activity, honors user-approved reclassifications (RECL1 — a correction moves activity WITH the event, so 4a–4d all see corrected recency and 4d auto-revive fires when a correction moves recent activity onto a dormant thread), and NEVER reads the deprecated `entities.json thread.last_activity` field — a fossil no code maintains (the F-61 cleanup autopsy). Do NOT inline your own max(ts) scan.

For each project in entities.json:

## 4a. Active projects — stale check (existing behavior)

For each project with `status: "active"`:

1. `last_event_date` = the Phase-4 derivation above for `project.id`. No entry → fall back to the project's `first_seen` (never to `last_activity`).
2. Flag as **stale** if `today - last_event_date > 14 days` AND last `decision` event for this project was >7 days ago.
3. Skip projects with `dont_forget_snooze` active.

## 4b. Active → Dormant transition proposal (v2.10.3 NEW)

For each project with `status: "active"`:

- If `today - last_event_date > 30 days` → propose the dormant transition **through the Living Brain rail (LB2 §3a — the migrated writer)**: `brain_proposals.propose(workspace_root, kind="dormancy", tier="confirm", fingerprint="dont_forget:<thread_id>", detector="dont-forget", evidence="<N> days quiet", render_line="this project is going quiet — move to Dormant?", ttl_days=30, thread_id=<thread_id>, action_tuples=[{"action": "confirm proposal"}, {"action": "dismiss proposal"}, {"action": "snooze proposal 7d"}], extra={"title": <project display name>})`. Do NOT append a bare `dont_forget_dormant_proposal` event any more — `propose()` owns dedup (an open row or a pre-migration legacy row with the same target suppresses re-proposing; a dismissal carries the shared 60d cooldown, superseding the old 14d prose cooldown) and the row surfaces in this chat AND on the staff meeting with real verbs. Still surface it in this chat's REVIEW section as before — the rail changes where the row LIVES, not where it shows.
- If `today - last_event_date > 60 days` AND the dormant proposal expired unanswered (its `brain_proposal_expired` tombstone exists — the 30d TTL passed with no user action; pre-migration fossil rows: no user action within 30 days of the legacy event) → **auto-flip to dormant**. Write `status_change` event with `data: {primary_thread_id, old_status: "active", new_status: "dormant", triggered_by: "auto", inactivity_days: <N>}`. Update entities.json via workspace-manager.

## 4c. Dormant → Archived transition (v2.10.3 NEW)

For each project with `status: "dormant"`:

- If `today - last_event_date > 180 days` → auto-flip to archived. Write `status_change` event with `triggered_by: "auto", inactivity_days: <N>`. Update entities.json.
- Dormant → archived is silent (no user prompt). Archived projects are still accessible by name; the user just no longer needs to see the dormant proposal repeatedly.

## 4d. Re-active detection (v2.10.3 NEW)

For each project with `status: "dormant"` OR `status: "archived"`:

- If new events appeared in last 7 days (the Phase-4 derivation shows activity for `project.id` newer than the status_change event — this credits `related_thread_ids` touches too) → auto-revive to `status: "active"`. Write `status_change` event with `triggered_by: "auto_revive", reason: "new_activity_detected"`.
- Re-revive is silent — the user just sees the project re-appearing in daily flows naturally.

This means `go [project]` + adding a session note (which writes events) auto-revives any dormant or archived project. No explicit `revive [project]` command needed for the common case.

# Phase 4e — Daily proposal surfacing (v2.10.3 NEW)

In addition to dormancy detection, Pulse surfaces high-confidence pending proposals from the beacon system (per `insight-generator/SKILL.md` Pass 9 + Pass 10):

1. **Read** `_hq/insights/.proposal_queue.jsonl` (project proposals from Pass 9) + `_hq/insights/.org_proposal_queue.jsonl` (org proposals from Pass 10).
2. **Filter** to high-confidence only (score ≥10 — strong-signal candidates that don't need to wait for Sunday's full review).
3. **Persist** each daily-surfaced proposal **through the Living Brain rail (LB2 §3a — the migrated org/project writer)**: `brain_proposals.propose(workspace_root, kind="org"|"project", tier="confirm", fingerprint="org:<name lowercased>"|"project:<name lowercased>", detector="dont-forget", evidence=<the queue row's signal line>, render_line=<the one-line ask>, action_tuples=[{"action": "confirm proposal"}, {"action": "dismiss proposal"}, {"action": "snooze proposal 7d"}], extra={"title": <entity name>, "name": <entity name>})`. The bp row IS the surfaced-daily mark — do NOT write a bare `org_proposal`/`project_proposal` event any more; the weekly insight-generator skips any candidate whose fingerprint has an open bp row, a resolution tombstone, or an active ledger cooldown (its edge case F).
4. **Cap** at 3 total daily proposals across both project + org types — same UX constraint as the weekly pass.

Pending proposals appear in the chat output's Quick Read closing block (Phase 8) as a sub-section, alongside the people-record review block. Same `a/b/c confirm/edit/skip` action set.

This catches obvious new orgs and projects same-day instead of waiting until Sunday — and lower-confidence proposals still batch in the weekly insight-generator review.

# Phase 4g — CRU review surfacing (v2.14.7+)

The CRU layer (`shared/scripts/cru_match.py` Paths 1, 2, 3, 4) writes `commitment_review_proposed` events when a downstream signal (Cowork send, native mail send, meeting transcript, inbound email) matches an open commitment with MEDIUM confidence (0.30 ≤ score < 0.55) — strong enough to investigate, not strong enough to auto-resolve. These need a user-facing surface for one-click confirm or dismiss; otherwise they sit in events.jsonl with no resolution path.

Pulse is the right surface because (a) it already aggregates "things that need your attention" across multiple signal types, (b) it fires daily so the queue drains naturally, and (c) the action set (`confirm` / `skip`) parallels the existing pending-review pattern.

**Phase 4g logic:**

1. Read events.jsonl. Find every `commitment_review_proposed` event from the last 7 days.
2. Filter out any whose `data.commitment_id` already has a subsequent `commitment_resolved` (HIGH-confidence path subsequently fired) OR `commitment_review_dismissed` (user already skipped) event. The aggregator pattern from `build_workspace_map_input.py` `_aggregate_commitments` works here — same shape.
3. Filter out any whose underlying commitment is itself already resolved (`commitment_resolved` / `thread_resolved` for the same commitment_id).
4. For each surviving review-proposed event, build a REVIEW item per the shape below.

**Per-item shape (CRU review — sub-namespace `r1/r2/...`):**

```python
{
    "n": 10,                                             # global numbering continues
    "icon": None,
    "name": "Sam Sample",                           # counterparty / recipient on the original commitment
    "context_tag": "Did 'Send pricing deck to Sam' get fulfilled? Sent via native mail Apr 30 with subject 'Q2 deck — final'. Match score 0.42 — likely but not certain. Done to mark the commitment fulfilled; Not relevant to keep it open in Commitments.",
    "actions": ["10 resolved", "10 not relevant"],  # v2.14.38+ — affirmative is `resolved` (mark commitment fulfilled), negative is `not relevant` (commitment stays open in Commitments view). MLK1 retired the `add to my list` deferral; not answering IS the defer — the item re-surfaces on a later fire. Replaces v2.14.7 confirm/skip pair.
}
```

Naming the `r` sub-namespace conceptually keeps these distinct from `a/b/c` (people-record reviews), `d1/d2` (dormant transitions), and `e1/e2` (entity proposals) — but the `n` field uses normal global numbering. The orchestrator can group r-items at the bottom of the REVIEW section after a/b/c/d/e items.

**context_tag rules (v2.14.7+):**

The CRU review's context_tag must answer plain-English: *what was the commitment, what's the evidence, and what does Confirm / Skip do.* Pattern:

> Did '<commitment title>' get fulfilled? <one-line evidence: "Sent via [native mail / Cowork / transcript at Apr 30 mentioned 'as discussed'"]. Match score X.XX — likely but not certain. Confirm to mark resolved; Skip to keep it open in Commitments.

Forbidden in the context_tag: internal IDs (`commitment_seq_<n>`), event-type names (`commitment_review_proposed`, `cru_match`), score precision beyond 2 decimals, file paths.

**Cap:** at most 5 CRU review items per fire. If more exist, sort by score descending and take top 5; the rest re-surface tomorrow.

# Phase 4h — Intro follow-up check (NEW in v3.11.1)

The `intro-broker` skill (v3.8.0+) writes `intro_followup_check` events scheduled 30 days out when an intro is drafted. Pulse is the surface that finds these once their scheduled date arrives and asks "did the intro land?" Without this phase the events sit in events.jsonl forever and the operator never gets the check-in.

**Phase 4h logic:**

1. Read events.jsonl. Find every `intro_followup_check` event where `data.scheduled_for` ≤ today (in workspace local time).
2. Filter out any whose `data.intro_event_seq` already has:
   - A subsequent `intro_landed` / `intro_didnt_land` event (operator already resolved this check).
   - A `chat_dismissal` event referencing this check's seq (operator already dismissed in chat).
3. Filter out any where BOTH intro recipients (`person_a_id` + `person_b_id` from the parent `intro_made` event) have an `interaction` event between them after the intro date — that's strong evidence the intro landed; auto-resolve silently with an `intro_landed` event and skip the surface.
4. For each surviving check event, build an INTRO-CHECK item per the shape below.

**Per-item shape (intro check — sub-namespace `i1/i2/...`):**

```python
{
    "n": 12,                                             # global numbering continues
    "icon": None,
    "name": "Sam Sample ↔ Bo Sample",                    # both intro recipients
    "context_tag": "Did this intro land? Drafted 30 days ago. Nothing back-and-forth between them since. Mark landed if they connected; didn't land if it went cold; snooze (14 days) to check back.",
    "actions": ["12 landed", "12 didnt land", "12 snooze 14d", "12 skip"],
}
```

**Action semantics:**
- `landed` → append the intro-landed lifecycle event. The intro counts as successful for relationship-graph + future intro-broker voice samples.
- `didnt land` → append the intro-didnt-land lifecycle event. Useful signal for intro-broker's "this counterparty type doesn't respond to this framing" pattern detection.
- `snooze 14d` → schedule the intro-followup-check to re-surface in Pulse 14 days from today.
- `skip` → standard 24-hour dismissal.

**Cap:** at most 3 intro-check items per fire. If more exist, sort by `scheduled_for` ascending (oldest first) and take top 3; the rest re-surface tomorrow.

**Add `intro_followup_check` to the "internal mechanics — excluded from cadence" list** in Phase 1 so these scheduled markers don't accidentally count as interactions for dormancy detection. Same treatment as `commitment_review_proposed` and `pack_run`.

# Phase 4f — Org-layer synthesis (NEW in v2.10.5)

Parallel to the people-layer synthesis in Phase 5 — every Friday Pulse run also re-checks the org layer. For each org in entities.json that has been touched by ≥3 events in the last 7 days OR has had its `relationship_type` field set ≥30 days ago without re-validation, recompute its inferred `tier` + `relationship_type` per the v2.10.3 inference rules in `references/ORG_AND_THREAD_MODEL.md` § "Discovery". Compare against the current values:

- **Aligned** (no drift): silently log a `tier_validated` event with `triggered_by: "weekly_synthesis"`. No user-facing surface.
- **Disagreement** (signal pattern shifted): the org's relationship has changed — a vendor became a recurring service_provider, a prospect closed and became a client, a former client went quiet and is now external/passive, etc. Surface in the chat output as a "Relationship drifted?" sub-section with one-key correction.

**Drift detection rules:**

- Volume tier crossed a threshold (e.g., 6 → 25 interactions over 30d → was `external`, now signals `secondary`).
- Signal pattern shifted (mostly inbound + 1-off purchase signals → recurring billing → was `vendor`, now signals `service_provider`).
- Status implies inactive (last interaction > 60d, but org is still flagged active) → propose tier downgrade to `passive`.
- Relationship_type contradicts current signal (was `client`, but no inbound payment-shaped emails in 90d AND outbound volume dropped → propose `prospect` or `service_provider` review).

**High-confidence drift** (≥3 consistent signals across mail + calendar + transcripts): auto-apply via people-crm + log `tier_change` event with `triggered_by: "auto_drift_correction"`. Surface inline as a one-liner ("✓ Auto-updated: Acme Corp shifted from vendor → service_provider based on recurring billing").

**Low-confidence drift** (1-2 signals or conflicting): queue to the chat-output review section, same `[a/b/c] confirm/edit/skip` action shape as the people-record review.

This is the Org-Layer parallel of the people-layer three-layer ingestion model: continuous live capture (every orchestrator emits org-mention events) + weekly synthesis (this phase) + on-demand `update [org]` (workspace-manager command). Catches drift over time so the workspace doesn't go stale.

# Phase 5 — People-layer synthesis (NEW in v2.10.2)

For each person mentioned in events.jsonl across the **last 7 days** (including those NOT flagged as dormant/pattern-break in Phase 3):

1. **Re-derive canonical fields** from accumulated signal:
   - **Latest org affiliation**: scan email signature blocks + calendar attendee org-domain in last 7d. If consistent across ≥3 sources, mark as confirmed.
   - **Latest role**: scan signature blocks + LinkedIn-style mentions in transcripts. Confirmation requires ≥2 consistent sources.
   - **Updated `last_interaction`**: max ts of last-7-day events.
   - **Updated cadence baseline**: re-compute from extended event history.
   - **Active threads**: which `primary_thread_id`s did this person appear in this week.
2. **Confidence-tier each derived change:**
   - HIGH (auto-apply): ≥3 consistent signals AND no contradicting signals. Write the update directly to entities.json via people-crm (canonical writer).
   - LOW (queue for review): 1-2 signals OR conflicting signals. Append to a "Pending review" list for the Phase 7 chat output.
3. Audit trail: every applied change writes a `person_record_update` event to events.jsonl with `data: {person_id, field, old_value, new_value, confidence, sources: [...]}`.

This synthesis runs whether or not the person triggered any cracks. It's the weekly compounding layer for the people-context substrate.

# Phase 6 — Score and rank cracks

Score the dormancy/pattern-break/stale-project signals from Phases 3-4:
- Dormant person: weight by relationship strength (interaction count × recency penalty)
- Pattern break: lower weight (lower confidence)
- Stale active project: weight by aging × open-commitments-count

Sort by combined score. Take **top 5**. Diversify: prefer mix of person/project items rather than 5 dormant people.

**Surface-preference filter (Phase 6 Loop 2 — before rendering).** Drop any item the CEO has taught the system to stop surfacing (insight-generator Pass 14 → `_hq/data/surface-preferences.json`):

```python
import sys; sys.path.insert(0, "shared/scripts")
from surface_preferences import load_surface_preferences, is_suppressed
prefs = load_surface_preferences("<abs workspace root>")   # treat-as-empty-if-missing
items = [i for i in items
         if not is_suppressed(prefs, "pulse",
                              item_class=("dormancy" if i.is_person else "stale_project"),
                              entity_id=i.person_id or i.project_id)]
```

Missing store → no-op. This only hides a surfaced prompt; nothing about the underlying relationship/project state changes. (Same filter every widget orchestrator applies.)

# Phase 7 — Memory updates (silent per Rule 9)

Append to events.jsonl:
- `connector_read` for events.jsonl scans
- `dont_forget_run` event with `data: {surfaced_count: N, suppressed_count: M, top_5: [item ids], people_synthesized: P, auto_applied: Q, pending_review: R}`
- For each item surfaced: `pattern_break_detected` event linking to person/project
- For each HIGH-confidence person update: `person_record_update` event (per Phase 5)
- The fire receipt — **ONE call to the canonical receipt helper (`shared/scripts/receipts.py`, v4.5.2 R1); NEVER hand-roll the receipt JSON** (the `dont_forget` kind spelling was one of FINDINGS F-49's misses): `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "pulse", fired_via=<the Phase 2.9 receipt_fired_via: manual|scheduled|catchup>, surfaced=surfaced_count, duration_ms=elapsed_ms, late_tier=<the lateness tier when note/degrade, else None>, extra_data={"errors": [], "telemetry": build_pack_run_telemetry(...)})` — `receipt_fired_via` is what Phase 2.9's helper returned, never guessed; telemetry silent per Rule 9, surfaces in `usage report` skill aggregation. (The `dont_forget_run` event above keeps its own payload contract — the shared reader counts a fire that wrote both as ONE run.)

# Phase 8 — Post the chat turn (v2.10.8+ — renderer-driven, ENFORCED)

**Mandatory execution contract (v2.10.8+):**

You MUST execute the renderer via `mcp__workspace__bash`. You MUST NOT hand-write or paraphrase the chat string.

**Step 0 — compute the "this-week pulse" header line (v2.14.11+):**

Before building the data view, scan events.jsonl for the last 7 days and count:
- **Resolved this week** — count of `commitment_resolved` events in window
- **Pending review** — count of OPEN `commitment_review_proposed` events (filter same as Phase 4g: not closed by subsequent `commitment_resolved`, not dismissed by `commitment_review_dismissed` for the same commitment_id)
- **Going quiet** — count of `pattern_break_detected` events in window

(MLK1 2026-07-21: the fourth segment — "captured to list", counting `commitment_to_discuss` events — is GONE. The list is retired and no capture path writes those events; a count of a dead lane is noise.)

Render as ONE plain-English line that becomes the chat-output header:

> *This week: 7 resolved · 3 pending review · 2 going quiet.*

Skip any zero-counts to keep the line lean. If ALL three counts are zero, omit the line entirely (no "0 across the board" placeholder — that reads as system noise).

Pass the rendered string into the data view as `header_pulse` (or directly as the first line of the `header` field). Per CONTRACT.md Rule 4: no internal jargon, no event-type names, no IDs.

**Step 1 — verify renderer imports (FIRST action of Phase 8):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from widget_transport import render_and_persist; from chat_output_renderer import validate_chat_output, CANONICAL_ACTIONS, CanonicalActionError, LeakDetectedError; print('OK')"
```

If stdout is not exactly `OK`, ABORT the fire and surface plain English: `(Renderer pre-flight failed — chat output deferred. Diagnostic: <error>.)` Do NOT post any widget.

**v2.13.0+ enforcement:** the renderer raises `CanonicalActionError` if any action verb is not in `CANONICAL_ACTIONS` (e.g., `mark expected` and the v2.13.0 `resolved [reason]` are both no longer canonical — v2.14.1 unified to plain `resolved`). It raises `LeakDetectedError` on any forbidden pattern (REVIEW jargon like `last_interaction proposed:`, schema fields, internal narration). It raises `DataShapeError` (v2.14.1+) on items with inconsistent shapes (e.g., email-shaped items missing required actions). All three blocking. Fix the data view, never the allow-list.

**Empty-state rule (v2.14.19+):** if no cracks surfaced (no stale projects, no person dormancy, no pattern breaks, no pending people-record reviews, no entity proposals, no CRU review proposals — a genuinely quiet day), DO NOT improvise a "Pulse — all clear" widget by hand-typing HTML. Build `data_view = {"widget_mode": "all_clear_summary", "header": "Pulse — nothing surfaced this morning", "sub_header": "<weekday>, <date> · <time> check", "counters": [{"label": "Active projects", "value": n_active}, {"label": "Stale projects", "value": 0}, {"label": "Person watch-list", "value": 0}, {"label": "Pending reviews", "value": 0}], "summary_line": "Everything's tracking — no projects past staleness, no people quieter than usual, no pending entity reviews. ...", "tracked_items": [], "footer": None}` and pass to `render_chat_output_widget()`. NEVER hand-build the empty-state widget. See `orchestrator-commitments.md` for the full diagnosis (v2.14.18 fresh-install bug).

**Step 2 — build data_view, render widget HTML, post via show_widget (v2.10.9+):**

```python
# (Inside python3 -c body invoked after the Rule 22 preamble + cd "$PLUGIN_ROOT")
import sys
sys.path.insert(0, "shared/scripts")
from widget_transport import render_and_persist

# Single section with all top-N cracks; pending people / dormant / entity proposals
# go in sub_items under appropriate parent sections (or as a closing review section)
data_view = {
    "widget_mode": "all_batch_widget",
    "source_skill": "pulse",  # W4 (Phase 3) — stamped into every Apply-all tuple as src; apply-choices dispatches on it statelessly (no 60-min fire-marker window)
    "header": f"{n_cracks} things worth not forgetting this morning.",
    "sections": [
        {"title": None, "count": None, "items": [item_for_crack(c) for c in top_cracks]},
        # Optional: review section for pending people / dormant / entity proposals
        {"title": "REVIEW", "count": n_review, "items": review_items} if n_review else None,
    ],
    "quick_read": quick_read,
}

# Strip None sections
data_view["sections"] = [s for s in data_view["sections"] if s]

transport = render_and_persist(data_view=data_view, wrapper="fragment",
                               persist_dir="<WORKSPACE>/_hq/.system/widgets",
                               name_hint="pulse")
# Pass transport["html"] to mcp__visualize__show_widget as widget_code (persisted page bytes, verbatim) (EW2+T, F-15 —
# shared/CHAT_ACTION_WIDGET.md § Transport). Never hand-compose or post-process the HTML.
```

The widget renders inline with per-item buttons; user clicks accumulate locally; "Apply all" fires `apply choices: [...]` consolidated payload that the `apply-choices` skill catches and dispatches through the reply handlers below.

**Step 3 — Post the chat-links section (v2.12.0+):**

After posting the widget, emit a second chat turn with markdown source links per item. Format per `shared/CHAT_ACTION_WIDGET.md` § "Post-widget chat-links section":

```markdown
**Links:**

1. [<Person name> — Open context](<URL from item's Open context metadata>)
2. ...
```

- Numbering matches the widget items exactly.
- Source URL = the URL embedded in the item's `Open context` metadata link (Gmail thread, Granola transcript, Drive doc — varies by item).
- For items with no Open context link, skip them in the Links section.
- For pending-review sub-items (`a/b/c`), `dormant transitions (d1/d2)`, `entity proposals (e1/e2)` — same numbering scheme, include if they have a source URL, skip if not.
- If 0 items have any linkable source, omit the block.

Per Sam's Apr 30 ask: source links go in regular chat below the widget, not inside the widget (where iframe sandbox blocks `computer://` and breaks reliable click-through).

**Per-item shape, person dormant/pattern-break (v2.14.28+ — enriched metadata per M's testing 2026-05-06 item #11: "we should add a little more info to these cards explaining what the deal is"; v2.14.38+ — `original_thread` MANDATORY when source is email/transcript per M's 2026-05-07 ask: *"if it is pulling from a source, I want you to link the source and if it is an email, I want you to display the thread like you do in the inbox task"*):**

```python
{
    "n": 1,
    "icon": "👤",
    "name": "Bo Sample",                              # resolved, NEVER person_NNN
    "subject": None,
    "context_tag": "You usually talk every 5 days. It's been 18.",   # PLAIN ENGLISH why-this-is-here (v2.10.9+)
    "original_thread": {                                 # v2.14.38+ MANDATORY when source is email/transcript
        "author": "Bo Sample <bo@example.com>",
        "date": "Apr 18, 2:11 PM",
        "subject": "NetSuite handoff timing",
        "body": "I'll send the updated mapping by end of next week — pulling in the new product hierarchy first...",
        "url": "<connector-returned thread URL>",
    },
    "metadata": [
        # v2.14.28+ — the customer needs enough on the card to make the action choice
        # (Investigate vs Draft re-engagement vs Schedule catchup) without further digging.
        # All 4 entries below are REQUIRED. Use explicit "no X tracked" / "(unknown)"
        # phrases when a field genuinely lacks data — never silent-omit.
        ("Last contact", "18 days ago — Apr 18, Slack DM about Q3 OKR"),  # date + most-recent specific interaction (email subject, meeting topic, Slack thread, decision touched). NOT just "18 days ago" alone.
        ("Why they matter", "Direct report · NetSuite migration lead · Q3 OKR owner"),  # relationship + project + role. Pulled from entities.json person record. If person has no role / org / project tied → surface "(no role tracked yet)".
        ("Open context", "[NetSuite handoff still pending — Bo owes the mapping doc](<connector-returned thread URL>)"),  # what's specifically open between you (commitment owed, dropped thread, decision pending). Markdown link to the open thread / commitment source. If nothing's tracked → surface "(no open thread tracked — `Investigate` will pull cross-references)".
        ("What's at stake", "NetSuite cutover Aug 4 — handoff doc gates 3 downstream tasks"),  # the consequence of NOT following up. Pulled from project/commitment metadata. If no clear stake → surface "(warm relationship at risk of going cold — typical pattern is silence → drift → lost over 60 days)".
    ],
    "actions": ["1 investigate", "1 draft re-engagement", "1 schedule catchup [when]", "1 resolved", "1 snooze 3d"],  # v2.14.38+ — snooze fixed at 3 days. MLK1 retired `add to my list`; `snooze 3d` is the one deferral.
}
```

The 4 metadata entries together answer: WHEN was the last touch + WHAT was it about (Last contact), WHO IS THIS PERSON to me (Why they matter), WHAT'S OPEN with them right now (Open context), WHAT HAPPENS IF I IGNORE THIS (What's at stake). Pre-v2.14.28 the cards averaged 1-2 metadata entries and several were just `Last contact: <N days ago>` with no story — too bare to drive action. v2.14.28+ requires all 4.

**`original_thread` rule (v2.14.38+ MANDATORY when source is email/transcript):**

When the Pulse item is surfacing a person because of a specific email thread, Granola transcript, or other linkable source (i.e. when the `Open context` row references a Gmail/Granola URL, OR `Last contact` references an email subject), the `original_thread` field MUST be populated. Same shape as commitments + inbox-triage:

- `author` — sender display name + email (or attendee names for transcripts)
- `date` — localized timestamp of the latest message (or meeting date for transcripts)
- `subject` — message subject (or meeting title)
- `body` — first ~800 chars of the message body (or relevant transcript section); truncate with ellipsis if longer
- `url` — the deep-link the source connector returns (mail thread URL / transcript URL); `connector_adapters/mail.py::deep_link` prefers the returned URL and degrades to no link if none (N8) — never synthesize a provider host

Pull via the seam-resolved mail thread-fetch tool (`discover_for_category("email", "get_thread", …)` → `discover_mail_thread_fetch_tool` fallback) or the transcript tool (`discover_transcript_tool`) at fire time. If the thread can't be retrieved (deleted, permission error), populate whatever fields ARE available (subject + author + date if known); body becomes `"(thread body unavailable — open it in your mail client to read)"` and url stays populated so the customer can still click through. Don't silent-skip.

Renderer wraps `original_thread` in a collapsible `<details>` block above the metadata, with a "↗ Open in [the source connector]" link prominently inside (label from the connector's returned URL host / provider label — never a hardcoded provider name). Mirrors the inbox-triage accordion one-for-one — same UX the customer already knows from inbox. Per M's 2026-05-07 testing on Sam's bare card: *"there was not a link to the email, the description was very sparse."* Without the `original_thread` block + link, the Pulse card surfaces a person tied to an old thread but gives the customer no way to see that thread without re-prompting.

**Self-commitments + dormant items with no traceable source** (no email/transcript anchoring the cadence break — pure cadence-decay flag) → no `original_thread` field, no `<details>` block. Plain card. The chat output's Sources section at the end of the turn carries `(cadence-decay flag, no source thread)` provenance instead.

**Renderer enforcement (v2.14.38+):** `chat_output_renderer.py` raises `PulseRichnessError` (extends `DataShapeError`) before render if a Pulse person-dormant item has any of the 4 mandatory metadata keys missing OR `Open context` references a Gmail/Granola URL but `original_thread` is absent OR `original_thread.url` is missing/empty. Same blocking model as `CanonicalActionError` — fix at the orchestrator level, never disable the validator.

**Source data for each entry:**
- **Last contact** — most recent `outreach_sent` / `meeting_held` / `slack_thread_touched` event in events.jsonl involving this person, plus the event's subject/topic.
- **Why they matter** — entities.json person record (`relationship_type`, `org_id`, `role`, `is_direct_report`) + projects where they're listed in attendees / committers.
- **Open context** — open `commitment` events where person is owner OR requester (whichever is "open with them"); falls back to most recent thread that hasn't been closed.
- **What's at stake** — project-level deadlines / milestones that depend on this person; or generic warm-relationship-decay framing for low-stakes contacts.

**Action label notes (v2.14.1+ — unified Resolved, no surprise textarea):**
- `resolved` (v2.14.1 — DROPPED the `[reason]` bracket per Bo's Apr 30 testing: clicking the resolved button surprised him with a "why is it resolved" textarea. Per CONTRACT.md Rule 6: action labels must be clear about what they do; surprise inputs violate that. v2.14.1 makes `resolved` a plain state change everywhere — same behavior as Commitments YOU OWE `resolved`. Suppresses the alert for 14 days. Display label: `Done` (UXR1 D6 — the verb_taxonomy label; "Resolved" is in LEGACY_DISPLAY_LABELS and banned on new renders per F-59). NO textarea, NO input affordance.
  - If users want to record context about WHY they're resolving, they can do so in their own notes — the system doesn't prompt for it. (The 14-day suppression is the durable effect; the reason text was captured but never re-surfaced anyway, so dropping it loses no functionality.)
  - Same display label as Commitments YOU OWE `resolved` ("Done"). Same behavior, same mental model: "this isn't open anymore." Now also same mechanic.
- `schedule catchup [when]` (v2.12.4+ — added `[when]` bracket so the widget exposes a free-text natural-language input). User types "next Tuesday afternoon", "this Friday at 4pm", "sometime next week". The reply handler parses the natural language and either (a) creates a tentative calendar invite at the parsed time + drafts the request email, or (b) drafts the request email asking for the user's stated window. Per M's Apr 30 ask: *"if we select schedule catch up — same thing as reschedule open box and ask when."*

**`context_tag` rules (v2.10.9+ — plain-English "why this is here"):**

The `context_tag` line answers *why this person is being surfaced today* in language M can read at a glance. Drop the engineer terms (`Cadence break`, `Pattern break (low signal)`, `Dormant`, `limited history`) — they're internal labels, not status descriptors.

| Situation | `context_tag` shape | Example |
|---|---|---|
| Person has ≥3 historical interactions and silence > 1.8× cadence | `You usually talk every X days. It's been Y.` | `You usually talk every 2 weeks. It's been 6.` |
| Person has ≥3 historical interactions and silence > 2.5× cadence | Same as above — the math speaks for itself; don't double-tag with "dormant" | (same shape) |
| Person has <3 historical interactions (limited history) | Lead with the open thread instead — drop the cadence frame entirely | `Open thread from MaintainX demo — never closed out.` |
| Person has limited history AND no clear open thread | `Last touched X days ago. No open thread tracked yet — worth a check-in?` | (same) |

**Never use these phrases in `context_tag`:** "Cadence break", "Pattern break", "low signal", "limited history", "obs=N", "Degraded baseline mode". They're internal mechanics — translate to plain English or omit.

**Cadence math display:** if rendering "every X days," round to the nearest natural unit — "every week" / "every 2 weeks" / "every month" / "every 3 days." Don't display "every 5.7 days." For very recent or very old interactions, stick to days. M reads this fast — don't make him do unit conversions.

**`Typical cadence` row in metadata is REMOVED in v2.10.9.** It duplicated info already in `context_tag` and surfaced jargon ("limited history") when there wasn't enough data. The `Last contact` and `Open context` rows stay — those carry independent signal.

**Per-item shape, stale-active project:**

```python
{
    "n": 2,
    "icon": "📁",
    "name": "Aspen Hardware Co",             # resolved name, NEVER project_NNN
    "subject": None,
    "context_tag": "Stale active.",
    "metadata": [
        ("Status", "active"),
        ("Last activity", "Mar 28 (31 days quiet)"),
        ("Last decision", '[Mar 12 — "Hold off on margin restructure until Q3 numbers land."](https://granola.ai/note/...)'),
    ],
    "actions": ["2 prep deep work", "2 investigate", "2 mark paused", "2 status check", "2 resolved", "2 snooze 3d"],  # v2.14.38+ — added `resolved` (the "this is done" outcome was missing); `snooze 3d` is the deferral (MLK1 retired `add to my list`).
}
```

**Per-item shape, review-pass items (people-record / dormant-transition / entity-proposal):**

These go in a separate "REVIEW" section, each as its own item with sub-letter numbering for the action token.

**REVIEW context_tag rule (v2.12.5+ — explicit "what Confirm does"):**

The `context_tag` for a REVIEW item must answer TWO questions in plain English:
1. **What's being proposed** (the change the system thinks should happen)
2. **What "Confirm" will actually do** (so the user knows what they're approving)

NEVER expose schema field names, "proposed:" syntax, or "(N signal, low confidence)" annotations. Translate every internal mechanic into a one-sentence statement the user can act on without reading documentation.

| Situation | FORBIDDEN context_tag | REQUIRED context_tag |
|---|---|---|
| Person has email matching org domain but no org_id set | `Quinn (acme.example.com) — no org linked — email domain acme.example.com suggests Acme Co affiliation` | `Link Quinn to Acme Co? His email is tate@acme.example.com. Confirm to link, Edit to pick a different org, Skip to leave unaffiliated.` |
| `last_interaction_date` updated, low confidence | `last_interaction proposed: Apr 14 → Apr 28 (1 signal, low confidence)` | `Update Andrea's last contact to Apr 28 (was Apr 14)? Confirm to apply.` |
| Role change proposal | `role_field proposed: VP Eng → CEO (1 source)` | `Update Sam's role to CEO at Summit Company? Confirm to apply.` |
| Org affiliation change | `org_id proposed: org_005 → org_007` | `Move Bo from Summit Company to Northstar Partners? Confirm to apply.` |
| Email address change | `primary_email proposed: foo@old.example.com → foo@new.example.com` | `Switch Bo's primary email to bo@northstar.example.com (was bo@example.com)? Confirm to apply.` |
| New org candidate | `Acme Co — new org candidate — 1 person (Quinn) using @acme.example.com` | `Add Acme Co as a new org? Quinn's email is @acme.example.com; 5 recent threads reference setup. Confirm to add as a prospect.` |

**Pattern: every REVIEW context_tag follows the shape `<verb the change> + <one-sentence reason> + Confirm-to-X / Edit-to-Y / Skip-to-Z?`** The verb at the front is what makes the action clear. "Link Quinn to Acme Co?" is unambiguous. "Quinn — no org linked — email domain suggests..." is not.

**Forbidden in REVIEW context_tag:** `last_interaction`, `proposed:`, `signal`, `confidence`, `low/high/medium confidence`, `(N source)`, `field updated`, schema field names of any kind, raw "(acme.example.com)" / "(@domain)" parentheticals (write as "His email is tate@acme.example.com"). The leak scanner catches several of these — see `chat_output_renderer.py` `scan_for_id_leaks()`.

**Don't surface the same signal twice.** If a person-record review (sub-letter `a`) and an entity-proposal (sub-letter `e1`) are both about the same domain match (e.g. `a` is "link Quinn to Acme Co" and `e1` is "add Acme Co as a new org"), they're effectively one decision. Surface them as a SINGLE merged item with the action set `Confirm both | Confirm just person | Confirm just org | Skip both`. Per M's Apr 30 ask: *"the two Acme Co items (a + e1) really are the same signal — confirming e1 should auto-resolve a."* The merger is at orchestrator-build time, not renderer-time.

```python
# People-record review (Phase 5 weekly synthesis output) — v2.14.5+ trailing
# finish-cluster (snooze + skip) appended after the type-specific actions for
# UX consistency across all three review-shaped item types.
{
    "n": 5,                                              # global numbering continues
    "icon": None,
    "name": "Andrea Wetsel",
    "subject": None,
    "context_tag": "I think you talked Apr 28 — was tracking Apr 14. Confirm or correct.",
    "metadata": [("Signal", "1 draft staged Apr 28 (team-plan account setup); no direct reply yet")],
    "actions": ["5 add [text]", "5 not relevant"],  # v2.14.38+ — REVIEW items consolidated: `add [text]` is the single affirmative (textarea, empty=accept inferred, non-empty=fold context into the entity); `not relevant` is the 60d-cooldown "no, don't apply this update". MLK1 retired the `add to my list` deferral; not answering defers naturally. Replaces v2.14.5 confirm/edit-[change]/snooze/skip cluster which had ambiguity between confirm-as-is and edit-then-confirm.
    # `edit [change]` opens a textarea so the user can type the corrected value.
}

# Dormant-transition proposal (Phase 4b) — v2.14.5+ finish-cluster appended
{
    "n": 8,
    "icon": "📁",
    "name": "Aspen Hardware",
    "context_tag": "currently paused, but new activity Apr 21 suggests revival",
    "actions": ["8 active", "8 keep paused", "8 archive"],  # v2.14.38+ — dormant transitions keep their type-specific state verbs (active/keep paused/archive — each is a distinct outcome). `snooze 3d` dropped: "should this go dormant?" with a 3-day reappear is annoying. MLK1 retired `add to my list`; no-action defers (the row surfaces again later). Skip removed for the same reason.
}

# Entity proposal (Phase 4e high-confidence) — v2.14.38+ unified REVIEW
# action set: `add [text]` (textarea — empty accepts inferred, non-empty
# folds context like relationship-type override into the entity record),
# `not relevant`. Replaces the v2.14.5 confirm-[type]/edit-[type]/snooze/
# skip cluster. (MLK1 retired the `add to my list` deferral.)
{
    "n": 9,
    "icon": "🏢",
    "name": "Acme Co",
    "context_tag": "Track Acme Co as a prospect org? Email domain acme.example.com seen in 5 threads. Add to track as a prospect (textarea lets you override relationship type), Not relevant to dismiss for 60 days.",
    "actions": ["9 add [text]", "9 not relevant"],
}
```

**Pre-build resolution rules:**
- Every `person_NNN` / `org_NNN` / `project_NNN` resolved to canonical display name BEFORE building data view
- Threshold rationales translated to plain English when relevant ("Still building cadence baseline — these flags will sharpen over the next few weeks") OR omitted entirely when not relevant
- No `obs=N`, no `Degraded baseline mode`, no `engagement-state should reclassify` — those are validator-banned

v2.10.5+ format: per-item action pills with `▸` markers (Rule 5), `N.` prefix REQUIRED on every item (Rule 3 — never silently dropped), all entity IDs resolved to plain English (Rule 1 — `org_015` → "Mr Test ORG"; `project_010` → "Aspen Hardware project"; never leak), no engineer-speak in rationales (Rule 1 — drop `obs=1`, `Degraded baseline mode for 14 of 17`, `engagement-state should reclassify`).

**Action surface (v2.10.9+ — all-batch button widget per `shared/CHAT_ACTION_WIDGET.md`):**

The action surface is a `show_widget`-rendered button group per item, with all selections batched and one "Apply all" submission at the end. See `shared/CHAT_ACTION_WIDGET.md` for the full spec.

Sub-letter items (`a/b/c` for pending review records, `d1/d2` for dormant transitions, `e1/e2` for entity proposals) batch alongside main items in the same widget. Each sub-letter item gets its own button row inside the parent item's group; selections from sub-letters and main items are submitted together in one `apply choices:` payload.

**No example rendered output is included by design (v2.10.8+).** Read `shared/scripts/chat_output_renderer.py` if you need to understand the output format. Execute the transport (`render_and_persist`); relay its page bytes (`transport["html"]`) as `widget_code` — the persisted render is sealed.

**Required visual structure for ALL Pulse items (consistent with rest of orchestrators):**

```
N. [icon] [Resolved Name — NO entity_id leak] — [Status descriptor].
   [Plain-English context, ≤3 lines.]
   [Optional inline source link (Rule 2).]

   ▸ N verb1  ▸ N verb2  ▸ N skip
```

Blank line BEFORE the pill row, blank line BETWEEN items.

**Sub-namespaces for review-shaped items:**
- `a` / `b` / `c` for pending people-record reviews (Phase 5)
- `d1` / `d2` / `d3` for dormant project transition proposals (Phase 4b)
- `e1` / `e2` / `e3` for entity proposals (Phase 4e high-confidence + new from Phase 4f weekly org synthesis)

Each sub-letter item ALSO gets its own pill row directly under it. Consistent shape across all sub-namespaces.

**No engineer-speak rationales (Rule 1):** drop "Degraded baseline mode for 14 of 17", "obs=1", "engagement-state should reclassify from paused → active", "signal_count=3" — those are internal mechanics. If the system is genuinely in early-baseline mode and that affects what's surfaced, say it in plain English ("Still building cadence baseline — these flags will sharpen over the next few weeks") and only when relevant. Never narrate per-item rationale numbers in chat.

For Pulse items, use this per-item first-line shape (Rule 6 — no redundant slug):

- Person: `[N]. 👤 [Full Name] — [Status: Cadence break / Pattern break (low signal) / Dormant].`
- Project: `[N]. 📁 [Project Name] — Stale active.`

Status sub-line: 1-2 sentences. Use Rule 2 inline source links to point at the supporting signal (recent email, last decision, last meeting). Make every claim drillable.

For the Quick Read block (Rule 7), include all of:
1. Per-item interpretation when N>2 and clustering signal exists (vendor-eval pattern, "all the same person", etc.)
2. The week's people-synthesis summary line + 0-3 pending-review person records (each with action token `[a/b/c] confirm`, `[a/b/c] edit [change]`, `[a/b/c] skip`)
3. **Dormant transition proposals (v2.10.3 + v2.14.5 finish-cluster)** — for each project flagged in Phase 4b (active >30d quiet) that hasn't been proposed in the last 14 days, surface a one-line proposal: `Aspen Hardware — quiet for 35 days. Move to Dormant? [d1] active / [d1] keep paused / [d1] archive / [d1] snooze [duration] / [d1] skip` (sub-letter `d1`, `d2` etc. namespaced for dormant proposals)
4. **High-confidence entity proposals (v2.10.3 + v2.14.5 — name-explicit context, finish-cluster)** — for each Pass 9 (project) or Pass 10 (org) high-confidence proposal queued from Phase 4e, surface a one-line item that NAMES the entity and explains what Confirm does, plus the standard finish-cluster: `Track Acme Logistics as a vendor org? 4 inbound emails, 1 invoice. [e1] confirm [type] / [e1] edit [type] / [e1] snooze [duration] / [e1] skip` (sub-letter `e1`, `e2` etc. namespaced for entity proposals — e for entity)

**Quick Read block omission rule (v2.10.9+ — fix for "feels random" footer M flagged Apr 29):**

The Quick Read block exists to surface meaningful synthesis. When all four sub-sections are empty — no clustering interpretation worth surfacing, no pending-review person records, no dormant transition proposals, no entity proposals — **omit the entire Quick Read block.** Do not render a footer of negatives ("People-layer synthesis: 5 people touched this week, all single-event signal. No stale-active projects. No dormant transitions to propose. No new entity proposals in queue.") — that reads as noise and obscures the real signal above it.

The Quick Read block earns space only when at least one of these is true:
- A clustering interpretation adds value (e.g., "MaintainX and CompanyCam are both Feb vendor demos that never got closed out — fastest action is `resolved [reason]` if the eval is done.")
- ≥1 pending-review person record (the `a/b/c confirm` block)
- ≥1 dormant project transition proposal (`d1/d2/...`)
- ≥1 entity proposal (`e1/e2/...`)

If none of these → no Quick Read block. The list of items above is the entire output.

**Empty-state output (zero items in main list):**

If 0 cracks AND 0 pending-review records AND 0 dormant proposals AND 0 entity proposals (i.e., truly nothing to surface today):

> Everything's running normally. ✓ N people synthesized this week, all auto-applied. All projects active and recent.

This is the only case where empty-state copy renders. Otherwise, keep the surface lean.

**Sources section at the end of the chat turn (per `_hq/CONVENTIONS_SOURCE_LINKS.md`):**

After items + optional Quick Read, append a `Sources:` section listing every connector source referenced in the items' Open Context links — Gmail threads, Granola transcripts, Drive docs. One bullet per source. Markdown links only. If no sources were referenced (no Open Context links in any item), omit the section.

# Phase 9 — Failure handling (Rule 8)

- Person-relationship math fails (insufficient historical data on first install): fall back to absolute thresholds. Log a `degraded_baseline` event. Surface plain-English: `(Still building cadence baseline — these flags will sharpen over the next few weeks.)`
- entities.json malformed: stop, log `scheduled_task_failure`, surface plain-English one-liner.
- People-CRM write fails on auto-apply: log to errors[], leave the synthesized change in pending-review for next run.

# Reply handling

Parse `N action` (with or without period). Sub-letter `a/b/c` for pending-review person records.

## Person dormancy / pattern-break actions

- `N investigate` → fire `tell me about [name]` chat skill. Cross-reference report.
- `N draft re-engagement` → run email-writer with re-engagement voice tilt. The drafted email surfaces in the apply-choices consolidated response widget per `apply-choices/SKILL.md` Step 4 — the standard email-card controls — Send / Draft / Snooze (3 days) one-tap buttons and the directly-editable body (FB-17; labels from the verb taxonomy; prose names only what the card shows, t3 FB-11) available inline (v2.12.4+). On `send`, follow §3c priority order. **Email-on-file check (v2.12.4+):** if the person has no email address recorded, the consolidated response surfaces the draft with the To field showing `(not on file — add before sending)` instead of internal jargon like `[Noah's email — missing in entities.json, fill before send]`. The user can fill the To field via the `add email then send` recovery affordance (Bug #44).
- `N schedule catchup [when]` (v2.12.4+ free-text input) → parse the user's typed natural-language window (`next Tuesday afternoon`, `this Friday at 4pm`, `sometime next week`). If parseable to a specific time, create a tentative calendar invite at that time + draft the request email; if just a window, draft the request asking for the user's stated availability. Draft surfaces in the consolidated response widget.
- `N resolved` (v2.14.1+ — dropped `[reason]`; v2.14.38+ unified verb across all surfaces) → the "expected / just busy" outcome on a person-dormancy item. NO input affordance — clean one-click action. Display label: `Done` (UXR1 D6 — the verb_taxonomy label; "Resolved" is banned on new renders). Confirmation: `✓ Done — <name>'s alert suppressed for 14 days.` Writes:
  1. **The 14-day suppression (made explicit, Phase 6).** Write a `dont_forget_feedback` event `{data: {person_id, feedback: "just_busy"}}` — this is the event Phase 3 step 6 already reads to skip the person for 14 days, and the event insight-generator Pass 14 mines. (Historically the 14-day suppression was implied; Phase 6 names the writer so the read/write contract is one thing. Also stamp `data.fingerprint`/`surface`/`item_class` per apply-choices Step 3f so Loop 2 can key on it.)
  2. **Quick Win B — widen the cadence baseline (the model update on top of the suppression).** Call `dormancy.record_just_busy(workspace_root, person_id, observed_gap_days=days_since, source_skill="pulse")`. This persists `cadence_override_days = max(existing, days_since)` on the person record via the canonical people writer, so the SAME gap no longer trips the flag in 14 days — the relationship model improves instead of being re-overridden. Never a direct entities.json write; `record_just_busy` returns None (silent no-op) on any error and never blocks the reply.
- `N snooze 3d` (v2.14.38+) → write `chat_dismissal` event with `data.snooze_until: <today + 3d>`. Person won't re-surface in Pulse until the date passes. Replaces v2.14.5 `snooze [duration]` (textarea version retained as deprecated back-compat alias only). (MLK1: `add to my list` — the old indefinite defer that wrote a `commitment_to_discuss` — is retired; no row emits it, and a persisted old widget's click dispatches through apply-choices with its original meaning.)

## Stale project actions

- `N prep deep work` → generate a context-loaded prompt for revisiting the project (same shape as the YOU OWE `prep deep work` prompt in `orchestrator-commitments.md`, adapted for stale-project context — "this project has been quiet for N days; let's revisit"). Pulls last 14 days of events for the project + last decision + any open commitments. Renamed v2.12.0 from `work on it` per Sam's Apr 30 feedback.
- `N investigate` → fire `tell me about [project]` cross-reference.
- `N mark paused` → write `status_change` event with `data: {primary_thread_id, new_status: "paused"}`. Project drops from Pulse. (v3.5.0+: was `project_status_change` pre-v3.5.0; consolidated to canonical `status_change` so MASTER_TRACKER Paused/Blocked column and DORMANT view actually see these flips — the non-canonical name was silently dropped from both views per the 2026-05-17 audit.)
- `N status check` → drafts an internal status-check email asking project owner where things stand. Lazy mail per `EMAIL_DRAFT_PROTOCOL.md`. On `send`, follow §3c priority order (Zapier → native threaded → standalone). Write `outreach_sent` with `via` field.
- `N resolved` (v2.14.38+) → mark project as resolved/done. Same semantics as commitment `resolved` — write a `status_change` event (v3.5.0+ canonical name; was `project_status_change` pre-v3.5.0) with `data: {primary_thread_id, new_status: "archived"}` and suppress for 14 days. Confirm: `✓ <project> marked resolved.`
- `N snooze 3d` (v2.14.38+) → 3-day snooze, same dispatch as person dormancy.

## Pending-review person record actions (v2.14.38+ — REVIEW unified action set)

- `[a/b/c] add [text]` (v2.14.38+) → opens textarea pre-populated with the inferred change rendered in readable form:
  ```
  Sam's role: CEO at Summit Company
  Source: 1 draft staged Apr 28 (team-plan account setup)
  Add anything you want me to fold in, or leave blank to accept as-is.
  ```
  User accepts inferred values by leaving the textarea blank, OR types corrections (e.g. "not CEO, just board chair" → applies that instead). On Apply, people-crm applies the change to entities.json. Write `person_record_update` event with `confidence: user_confirmed` (or `user_corrected` if textarea non-empty + override applied).
- `[a/b/c] not relevant` (v2.14.38+) → discard the proposed change. Write `person_record_update_rejected` event with 60-day cooldown — same low-confidence signal won't re-surface for 60 days. (Replaces the v2.14.5 `skip` with stronger semantics; 60-day vs 30-day reflects "this isn't right, don't ask again soon." MLK1 retired the `add to my list` deferral; not answering defers naturally.)

## Dormant transition proposal actions (v2.14.38+ — state verbs preserved; LB2 rail split)

**LB2:** a NEW dormancy row is a `bp_` proposal (the Phase 4b migrated writer) — its decline path is `brain_proposals.resolve_proposal(workspace_root, <bp_ id verbatim>, "declined", resolved_by=<user person_id>, source_skill="pulse")` (tombstone + the shared 60d cooldown). A PRE-migration fossil row (legacy `dont_forget:` adapter id, no `bp_` prefix) keeps the legacy writes below — the adapters render those rows until they resolve or age out, and their handlers never change.

- `[d1/d2/...] active` → keep the project active. bp row → `resolve_proposal(..., "declined")`; fossil row → write `dont_forget_dormant_proposal_declined` event (14-day cooldown, legacy semantics preserved).
- `[d1/d2/...] keep paused` → for projects already paused; no status change. Same rail split as `active`.
- `[d1/d2/...] archive` → skip the dormant step entirely; write `status_change` event direct to archived, then bp row → `resolve_proposal(..., "applied")`; fossil row → the status_change is its natural tombstone (60-day cooldown).

(MLK1 retired the `[d] add to my list` indefinite defer. "I'll think about it" is now simply not answering — the row stays open and surfaces again later; a defer is not an answer.)

## CRU review actions (v2.14.38+, sub-namespace `r1/r2/...`)

Each CRU review item is a single MEDIUM-confidence match between an outbound signal (Cowork send / native mail send / meeting transcript) and an open commitment. The user resolves it, dismisses it as not relevant, OR defers.

- `[r1/r2/...] resolved` (Stage E 2026-07 — THE closure path; supersedes the v2.14.38 direct write) → `commitment_state.close_commitment(workspace_root, <underlying commitment_id verbatim>, resolved_by=<the commitment owner>, evidence=<the review proposal's evidence>, source_skill="pulse", user_confirmed=True)` — the explicit click IS user confirmation, so this closes even a `pending_review`-flagged commitment. Never hand-build the `commitment_resolved` append. The original commitment drops from "you owe / they owe" on the next Commitments fire. Confirm: `✓ Marked '<commitment title>' as resolved.`
- `[r1/r2/...] not relevant` (v2.14.38+) → write `commitment_review_dismissed` event with 60-day cooldown referencing this commitment_id. The underlying commitment STAYS OPEN in Commitments (user explicitly rejected this signal as fulfillment). Confirm: `✓ Skipped — the commitment stays open in your Commitments view.` (MLK1 retired the `add to my list` defer; an unanswered review re-surfaces on a later fire.)

Both `resolved` and `not relevant` handlers close the review-proposed event implicitly via the new closing event — same pattern as entity-proposal handlers. No mutation of the original `commitment_review_proposed` event (events.jsonl is append-only).

## Entity proposal actions (v2.14.38+ — REVIEW unified action set)

Per M's v2.14.3 preview-cycle ask, the question always names the entity and explains what action will do. v2.14.38+ unifies the affirmative + negative + defer cluster across all REVIEW surfaces.

- `[e1/e2/...] add [text]` (v2.14.38+) — opens textarea pre-populated with the inferred entity details rendered in a readable form:

  ```
  Name: Acme Co
  Domains: acme.example.com
  Relationship type: prospect
  Scope: external
  Signal: 5 threads reference @acme.example.com; 1 calendar invite
  Add anything you want me to fold in (e.g. "actually they're a vendor, not prospect"), or leave blank to accept as-is.
  ```

  User accepts as-is by leaving the textarea blank, or types corrections to override the inferred fields. On Apply, workspace-manager `create_org` (or `create_project` for project proposals) parses the inferred OR user-corrected fields. **LB2 rail split:** a NEW entity row is a `bp_` proposal (the Phase 4e migrated writer) — after the entity write, `brain_proposals.resolve_proposal(workspace_root, <bp_ id verbatim>, "applied" (or "edited" when the textarea was non-empty), resolved_by=<user person_id>, source_skill="pulse")`; a PRE-migration fossil row is tombstoned naturally by the entity now existing (the adapter's existence check). Either way, also mark `surfaced_daily_resolved` in classifier_feedback.jsonl (the learning-loop record). Confirm: `✓ Acme Co added as a prospect.` (or with corrections applied).
- `[e1/e2/...] not relevant` (v2.14.38+) → bp row → `resolve_proposal(..., "declined")` (tombstone + shared 60d fingerprint cooldown); fossil row → write `org_proposal_declined` event with 60-day fingerprint cooldown (legacy semantics preserved). Won't re-surface for 60 days. (MLK1 retired the `add to my list` defer; an unanswered proposal re-surfaces on a later fire.)

The same `[e1] / [e2]` namespace handles project-proposal items too (rendering uses 📁 icon for projects vs 🏢 for orgs). Same action set; workspace-manager invokes `create_project` for project proposals and `create_org` for org proposals based on the proposal type recorded in the queue file.

For unrecognized → respond in plain English: "Reply with the item number + action — e.g. `1 investigate`, `2 mark paused`, `a confirm` for pending-review records."

# What this orchestrator does NOT do

- Does NOT auto-draft re-engagement emails (only on explicit `N draft re-engagement`).
- Does NOT modify entities.json directly except via people-crm canonical writer (Phase 5 auto-apply path).
- Does NOT surface decision-pending items from cr-past-meetings (those stay in their own chat).
- Does NOT surface every minor cadence wobble (top 5 by signal strength only — quality over quantity).
- Does NOT replace `cleanup` (broader workspace health pass — separate skill, M-triggered).
