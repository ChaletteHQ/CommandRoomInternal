# Event-Type Registry (Phase 1 Foundation, 2026-07)

**Enum home (decided once, final):** the event-type enum lives in
`shared/data-schemas/events.schema.json` — nowhere else. It is machine-read by
two enforcers, so there is no second copy to drift:

1. **Write time** — `shared/scripts/event_gate.py` (the `append_event()`
   gatekeeper, wired inside `atomic_append_jsonl`'s events.jsonl branch)
   validates every appended event's `type` against the enum via
   `shared/scripts/event_types.py`. Strict (reject) on BOTH entries since
   Phase 4, 2026-07-02 — the burn-in warn-only posture is over.
2. **Static guard** — `tests/run_source_of_truth_test.py` Check 4 fails the
   battery when any documented `"type": "<name>"` literal in skill prose or
   shared scripts is absent from the enum.

> Corrected 2026-07-25: this list used to name a third enforcer — "weekly-audit
> validates live events against the same schema." There is no weekly-audit
> skill in core (`skills/` has `cleanup` and `system-health`), and
> `is_known_type` has exactly one caller: the write gate. **Nothing validates
> event types on the read side.** The same stale claim is echoed in
> `event_types.py`'s module docstring. Do not rely on a read-side check that
> does not exist; see the fossil section below for what real substrate holds.

## Registering a new event type

1. Add the name to the `type` enum in `shared/data-schemas/events.schema.json`.
2. Add a row to the table below naming the **writer** and at least one
   **named consumer** (no consumer-less writes — Writes-checklist item 5,
   enforced at review). If the payload has a required shape, also add it to
   `shared/data-schemas/event-payloads.schema.json`.
3. Never write a type that isn't registered; never spell-variant an existing
   type (the gate normalizes known drift — e.g. `commitment_update` →
   `commitment_updated` — but new drift is a defect).

## Pre-registry fossils — read-tolerated, never writable (2026-07-25)

Real substrate contains event types that are NOT in the enum and are not
going to be. A survey of a live workspace found **52 unregistered types across
216 rows** (of 5,689 total); every one predates the gate going strict, and the
newest fossil write is dated **2026-07-02** — the very day Phase 4 closed the
write path. Nothing has written an unregistered type since, because nothing
can.

**They stay unregistered on purpose.** Three reasons, in order of weight:

1. The enum is the **write permission list**. `is_known_type` has exactly one
   caller — `event_gate`, on append. Registering 52 dead types would
   re-legalize writing every one of them and undo the drift fix this registry
   exists for.
2. Registration requires a **writer and a named consumer** (rule 2 above — no
   consumer-less writes). These have neither. Registering them would break the
   registry's own admission rule 52 times over.
3. `run_source_of_truth_test.py` Check 4 is **green**, which proves no current
   skill prose or shared script declares any of them as a write. They are
   historical rows, not a live code path.

The machine-readable list lives in
`shared/scripts/event_types.py::PRE_REGISTRY_FOSSILS`, with
`is_pre_registry_fossil()` as the read-side classifier. Its job is to let an
auditor distinguish **an expected historical row** from **a new unregistered
type, which is a defect**. It is never consulted on the write path.
`tests/run_event_fossils_test.py` keeps the set honest (disjoint from the
enum; no fossil declared as a write by current code) and — since 2026-07-25 —
anchors it to substrate: `tests/fixtures/event_fossils/_hq/data/events.jsonl`
carries one real-shaped row per fossil, and the suite asserts the fixture's
unregistered types and this list are the same 52 in **both** directions. So a
brand-new unregistered type shows up as a defect instead of being quietly
appended here, and a deleted fossil goes red too. That guard exists because
the whole DOCUMENT-not-register ruling rests on an eight-minute margin — the
newest unregistered write is `2026-07-02T13:54:43Z`, the commit that made the
legacy append path strict is `14:02:43Z`.

Notes on the families, for anyone reading old rows:

| Family | Types | Rows | What it was |
|---|---|---|---|
| `apply_choices_*` (4 spellings), `apply_dispatch`, `chat_action`, `probe_click` | 7 | 54 | Pre-gate widget/dispatch telemetry. The surviving lane is `triage_feedback` + the receipt contract. |
| `pending_review`, `pending_review_resolved`, `pending_review_skipped`, `decision_pending`, `person_review_pending`, `org_review_pending`, `pending_enrichment` | 7 | 55 | The pre-LB1 proposal queue. Superseded by `brain_proposal` / `brain_proposal_resolved`. |
| `cracks_watch_*` | 3 | 11 | The retired "cracks watch" pass. Its role was inherited by the Pulse chat, which LIFECYCLE1 retired in turn; what survives is `brain_proposal` + the weekly `lifecycle` job. |
| `org_added`, `org_archived`, `org_deleted`, `org_membership`, `org_proposal_confirmed` | 5 | 9 | Pre-`org_writer` prose org lifecycle. Superseded by `org_created` / `org_updated`. |
| `person_context_*`, `person_enrichment_pending`, `person_merge_proposed`, `person_record_review_queued` | 5 | 9 | Pre-`people_writer` prose person lifecycle. Superseded by the PID1 lane. |
| `session_close`, `session_end`, `scan_completed`, `substrate_cleanup`, `schedule_updated`, `schedule_skipped` | 6 | 14 | Assorted pre-receipt-contract run markers. Superseded by `shared/RECEIPT_CONTRACT.md`. |
| everything else (one-off prose writes: `follow_up`, `artifact_*`, `correction`, `owner_remap`, …) | 19 | 64 | Individually hand-written by pre-gate skill prose; no family, no successor lane. |

Seven fossils are near-misses of a registered name — read them as the
registered type, never re-mint the fossil spelling:
`commitment_update` → `commitment_updated` (the gate already normalizes this
one on append), `corruption-recovery` → `corruption_recovery` (hyphen; the
underscore form is what `recover_corruption.py` writes — the hyphenated
spelling in `RELIABILITY.md` refers to a `_hq/CONFLICTS.md` log line, a
different artifact), `apply_choices_audit` → `apply_choices_applied`,
`meeting_reprocessed` → `meeting_processed`, `reclassification_batch` →
`reclassification`, `pending_review` → `person_pending_review`,
`schedule_updated` → `schedule_created`.

## 2026-07 wave vocabulary (registered up front, Phase 1)

These types are registered once, here, so every later phase's
source-of-truth check passes without re-opening the enum. A type listed
below with a future phase is registered-but-not-yet-written; that is
expected until its phase lands.

| Type | Writer (phase) | Named consumers |
|---|---|---|
| `skill_customization_added` | skill_custom_writer.py (Phase 7, SCL1) | usage-report, coach, cleanup |
| `skill_customization_removed` | skill_custom_writer.py (Phase 7, SCL1) | usage-report, coach, cleanup, update-bridge (re-promotes never re-impose removed seeds) |
| `skill_customization_updated` | skill_custom_writer.py (Phase 7, SCL1) | usage-report, coach, cleanup |
| `skill_customization_reset` | skill_custom_writer.py (Phase 7, SCL1) | usage-report, coach, cleanup |
| `skill_customization_review` | insight-generator Pass 12 distiller (Phase 7, SCL1) | insight-generator (28-day review gate), usage-report |
| `onboarding_seed_ingested` | command-room-onboarding seed hook (Phase 7, Spec 3) | coach, usage-report, update-bridge |
| `schedule_config_healed` | reliability watchdog / change-schedule heal path (Phase 3) — registered for the heal path; R2 landed FLAG-ONLY under sparse-config semantics (no safe additive heal identified: the only orphan-override remedy is a removal, which cleanup never does), so nothing writes it yet | cleanup (Monday note), insight-generator |
| `schedule_parity_checked` | cleanup schedule-parity check (Phase 3, R2) — one audit event per weekly check with ghost/orphan mismatch counts; detect + report, NO config writes | cleanup (Monday note), insight-generator, usage-report |
| `schedule_add_proposed` | cleanup Monday note via schedule_proposals.py (Phase 3, R3) — one event per surfaced later-add proposal; the suppression record (no re-propose for 6 weeks) | schedule_proposals (suppression check), usage-report |
| `late_fire` | `late_fire.check_lateness` on scheduled-context fires ONLY, against an unserved slot (Phase 3 R4; served-slot ledger + run-mode gate v4.5.2 R2 — manual fires and schedule-change re-anchors never write it; carries `data.fired_via: catchup`) | cleanup (chronic-lateness detection), insight-generator (better-default-time proposals), system-health (R3 cadence truth) |
| `clock_untrusted` | `late_fire._clock_field` via `trusted_now`, ONCE PER PROCESS, only when the machine clock is provably wrong (SPEC CLOCK1) — `{direction: stale|ahead, source, skew_seconds, machine_now, corroborated_now}`. Telemetry only: it never blocks a fire, and a workspace with a healthy clock never writes one | cleanup (chronic-skew detection), insight-generator |
| `pulse_run` | Pulse orchestrator (Phase 3/6 quick win B) | insight-generator (cadence baseline), usage-report, value-receipt |
| `triage_feedback` | apply-choices, on every inbox action at dispatch (Phase 6, Loop 1) — `{sender, domain, bucket_assigned, action_taken, draft_offered}` via `triage_feedback.build_triage_feedback_event` | insight-generator Pass 13 (sender-priority proposals), usage-report |
| `prep_feedback` | orchestrator-past-meetings grades the prep brief against the transcript after meeting-notes runs (Phase 6, Loop 3) — `{meeting_id, meeting_type, sections_hit, sections_rendered, sections_missed, unpredicted_topics}` via `prep_grading.build_prep_feedback_event` | insight-generator Pass 15 (section-weight proposals), value-receipt |
| `prep_brief` | BOTH prep paths — the morning-brief fire's prep leg (SPEC BRIEFMERGE Phase 2.95, `generated_by="morning-brief"`; before that, the retired upcoming-meetings chat) AND call-prep on-demand 'prep me' — one per Call_Prep brief saved, via `receipts.log_prep_receipt` ONLY (v4.5.2 S1; F-29/F-29b) — `{meeting_id, slug, artifact, generated_by, fired_via, refreshed}`. NOT a task-run receipt: five briefs in one fire are five prep_brief events and ONE pack_run | morning-briefing no-prep detection (`receipts.prep_exists_for_meeting` — the "no prep" flag may only render when NO receipt exists for that meeting id), the prep leg's / call-prep's refresh-in-place check, value-receipt |
| `prep_weight_proposal` | insight-generator Pass 15 (Phase 6, Loop 3) — one per user decision on a proposed call-prep section-weight change; the applied weight lands in the call-prep skill config (`_hq/data/skill_config/call-prep.json`) | call-prep (reads section weights before rendering), usage-report |
| `extraction_hint_proposal` | insight-generator Loop 5 pass (Phase 6, Round 3) — one per user decision on a proposed extraction hint; the applied hint appends to `_hq/data/extraction-hints.md` | meeting-notes (extraction prompt), cru_match (resolution language), usage-report |
| `exemplar_update_proposal` | insight-generator Pass 16 (SPEC OUT8) — one per user decision on a proposed workspace-exemplar update (`{user_action, fingerprint, kind}`); the approved skeleton is written to `_hq/exemplars/<kind>/exemplar_1.md` via `exemplars.promote_workspace_exemplar` (scrub-gated, previous version rotated to `exemplar_2.md`) | insight-generator (60-day fingerprint cooldown via `proposal_ledger`), every STANDARD_KINDS composer (reads the exemplar at render time via `exemplars.get_exemplar`), usage-report |
| `sender_priority_proposal` | insight-generator Pass 13 (Phase 6, Loop 1) — one per user decision on a proposed sender/domain priority rule (`user_action` applied/edited/declined/skipped, `fingerprint`); the applied rule lands in `_hq/data/sender-priority-rules.json` | insight-generator (60-day fingerprint cooldown via `proposal_ledger`), usage-report |
| `surface_preference_proposal` | insight-generator Pass 14 (Phase 6, Loop 2) — one per user decision on a proposed suppression; the applied rule lands in `_hq/data/surface-preferences.json` | insight-generator (60-day cooldown), every widget orchestrator (reads the store to filter), usage-report |
| `confidence_override_proposal` | insight-generator Loop 4 calibration (Phase 6, Round 2) — one per user decision on a proposed match-score threshold change; the applied value lands in `_hq/data/confidence-overrides.json` | `confidence.py` accessors (read the override), cru_match (thresholds), usage-report |
| `chase_policy_proposal` | insight-generator Pass 7b propose-and-apply (Phase 6, Loop 6) — one per user decision on a per-relationship-type chase window; the applied rule lands in `_hq/data/chase-policy.json` | orchestrator-commitments (chase cadence), email-writer (follow-up drafts), usage-report |
| `commitment_noise_proposal` | insight-generator S3 rider (Phase 6, Round 2) — one per user decision on a proposed never-track rule; the applied rule appends to `_hq/config/commitment-rules.md` | every commitment producer (capture floor reads the rules file), usage-report |
| `dont_forget_feedback` | Pulse `resolved` on a person-dormancy item ("expected" / "just busy") writes it with `{person_id, feedback}` (Phase 6 Loop 2/quick win B made the writer explicit — it drives the existing 14-day suppression the dormancy pass already reads at Phase 3 step 6, and Quick Win B ALSO widens `cadence_override_days`) | Pulse dormancy pass (14-day suppression skip), insight-generator Pass 14 (dismissal mining, BOTH families), usage-report |
| `session_sweep_run` | session-sweep nightly skill (Phase 5, R1) — one audit event per run: sessions scanned, events recovered | cleanup (R10 scheduled-output self-audit), value-receipt, weekly-recap |
| `session_backfill_run` | session-sweep historical backfill (Phase 5, R2) — one audit event per confirmed backfill batch | cleanup, value-receipt, usage-report |
| `maintenance_run` | `maintenance_dispatcher.maintenance_receipt` (MAINT1) — exactly one per `maintenance` task fire: `{fired_at_slot, jobs_due, jobs_completed, jobs_failed, skipped_disabled}`. The dispatcher's own receipt; each job's success stays its own receipt type | `maintenance_dispatcher.validate_maintenance_ran`, task_watchdog (task freshness + `check_maintenance_jobs` gate), usage-report |
| `m1_voice_proof_shown` | command-room-onboarding Phase 5b voice proof (shipped v4.4.0; registered here retroactively — it was written without enum registration, the exact drift this registry stops) | usage-report, coach (onboarding-beat telemetry) |
| `commitment_reclassified` | `commitment_state.promote_task_to_commitment` (Phase 2 Stage D — triage `make task`/`promote` verbs) + `shared/scripts/migrate_commitment_kinds.py` (S6 one-time partition, dry-run default) | the projector (`load_open_commitments` kind-override fold), `commitment_counts` by_kind, commitment-triage, `stale_tasks` |
| `commitment_reopened` | `commitment_state.reopen_commitment` (Phase 2 Stage D — S4 triage undo; also the reconcile-sent `undo` affordance may migrate here) | the projector (`load_open_commitments` order-aware closure state), `close_commitment` idempotency (a reopened item may be re-closed), commitment-triage |
| `commitment_reassigned` | `commitment_state.reassign_commitment` (v4.6.0 S4 — the `reassign to [name]` verb + the "that's actually [name]'s" chat phrase; W4b's Theirs→[name] confirm verb dispatches it with `confirmed: true`) | the projector (`load_open_commitments` reassignment fold — latest event wins; unconfirmed reassignments stamp `pending_review` so the item sits in the unconfirmed bucket and never enters chase), `commitment_counts` direction buckets, commitment-triage, the daily Waiting On / My Plate chats (CTS1; pre-split: the Commitments chat) |
| `commitment_partial_received` | `commitment_state.mark_partial_received` (v4.6.0 MC1 — the per-person `mark received from [name]` verb on a multi-counterparty commitment; records "counterparty X delivered" WITHOUT closing the item) | the projector (`load_open_commitments` receipt fold — accumulates `data.received_from` / `data.received_from_names`, stamps `data.all_counterparties_received` when the roster is complete), the daily Commitments chase fan-out (drops received counterparties from the per-person nudge set), the closure PROPOSAL (all-received → propose close, never auto-close) |
| `chat_dismissal_cleared` | `mute_ledger.clear_dismissal` / `clear_dismissals` (v4.6.0 S4 — the Unmute verb on the `show muted` ledger + the triage batch-undo's mute reversal, F-20 P3a) | `mute_ledger.live_mutes` / `active_dismissal_target_ids` (THE dismissal-liveness readers — every surface that filters on an active `chat_dismissal` honors the clear), show-my-list render filter, relationship_moves exclusion pass, the Commitments/Pulse/Inbox orchestrator dismissal filters |
| `person_proposal_resolved` | apply-choices confirm-section dispatch via `confirm_flow.build_person_proposal_resolved_event` (v4.6.1 W4b — the proposal tombstone: `Add person` writes it with `resolution: person_added` after `people_writer.create_person`; `Same as [existing]` with `resolution: same_as` after `people_writer.add_person_alias`; `Not relevant` with `resolution: not_relevant`, nothing else written) | `confirm_flow.load_open_person_proposals` (adjudicated proposals stop re-surfacing — the F-46 P2b stranding fix), usage-report |
| `visual_gate` | `visual_gate.log_visual_gate` (SPEC OUT2 §3 — one per STANDARD_KINDS .docx the render-then-critique pass examined: `{doc, rendered, findings, fixed}`, plus `skipped_reason` when the preview ladder returned None; WARN-ONLY FOREVER at the code layer — the gate is judgment, not schema) | usage-report / insight-generator (mine it to prove the gate fires and to spot recurring visual defects; audit-trail type per the pack_run precedent — no code-shaped reader by design) |

## Connector-agnostic lane (connector-agnostic-v1, 2026-07-11)

Registered up front (Phase 1) so every later phase's source-of-truth check
passes without re-opening the enum. Each is registered-but-not-yet-written
until its phase lands (writer phase noted). Contract: `shared/ACCOUNT_SCOPE.md`
(Layer B/C). Owner of the `workspace.connectors` / `workspace.accounts` blocks
these describe = `workspace-manager` (WORKSPACE_API ownership map); onboarding
and update-bridge write only as declared delegates through the
`connector_config.py` setter.

| Type | Writer (phase) | Named consumers |
|---|---|---|
| `connector_detected` | drift-detect in workspace-manager / a silent maintenance task (Phase 4, C2) — one event when a new MCP server-id or account address first appears; carries `{server_id, provider?, fingerprint_matched?}`. Silent/scheduled fires only FLAG (R13), never prompt | command-room-onboarding (classify-before-use gate), workspace-manager (drift reconcile + fingerprint re-pair confirm), usage-report |
| `connector_backend_changed` | workspace-manager `set my email backend to [connector]` verb via the `connector_config.py` setter (Phase 4, C1); update-bridge additive migration writes it through the same delegated setter (N1) | `connector_config` readers (declared-backend resolution), command-room-update-bridge (migration idempotency), usage-report |
| `account_classified` | command-room-onboarding account-enumeration gate (Phase 4, R11) + workspace-manager classify verbs, both via the `connector_config.py` setter — `{address, role, surface, write_to_business, binding_verified}` | the writer wall (`account_scope_gate.enforce_scope` at the `atomic_append_jsonl` chokepoint in atomic_write.py + `account_scope_gate.enforce_record_scope` in `people_writer`/`org_writer` — R2/R3), `connector_config` scope readers, usage-report |
| `account_role_changed` | workspace-manager reclassify verbs (`[address] is my personal account`, `mark [account] out of scope`) via the setter (Phase 4, R10/C6) — `{address, old_role, new_role, old_dials, new_dials}`; a business→personal transition also emits `account_scope_masked` for the silent window | `connector_config` scope readers, the tombstone machinery (R5), usage-report, cleanup (misclassification audit trail) |
| `account_scope_masked` | the tombstone machinery on a business→personal reclassification (Phase 4, R5/R10) — an IN-PLACE scope mask (never a row move): `{address, masked_account_id, from_seq?, reason}`. Readers honor it; events.jsonl rows are never physically moved (seq/source_event_seq chains, closure refs, dedup idempotency, `.source_refs.idx` all depend on them) | every substrate reader's scope-mask honor pass (people-view, CRU projector, dormancy, relationship-moves), cleanup, usage-report |
| `account_scope_restored` | the tombstone machinery on a personal→business restore (Phase 4, R10) — un-masks previously masked rows + offers a rescan: `{address, masked_account_id, reason}` | the same scope-mask readers (restore path), command-room-onboarding (rescan offer), cleanup, usage-report |

## Reminder lane (v4.6.0 W4a)

| Type | Writer | Named consumers |
|---|---|---|
| `reminder` | `shared/scripts/reminders.py` builders, routed from the user's "remind me about X on [day]" (show-my-reminders owns the phrase family), appended via `event_gate.append_event` | morning-briefing (Pinned + Upcoming reminders sections), show-my-reminders, `reminders.active_reminders` |
| `reminder_updated` | `reminders.build_reminder_updated_event` — "push it to Friday" (push), "keep" (acknowledged touch, resets the escalation clock), wording fixes (edit) | morning-briefing, show-my-reminders (the reader folds pushes into the effective pin date) |
| `reminder_cleared` | `reminders.build_reminder_cleared_event` — "done with the reminder" | morning-briefing, show-my-reminders (one-shots deactivate; repeating reminders re-arm to the next occurrence — derive-next-on-read, NO scheduler) |

Hard rules (gate-enforced in `event_gate.py`, unconditional — both entries,
strict or legacy):

- **`data.origin` must be `user_explicit`.** No skill, sweep, or scheduled
  task may ever mint or mutate a reminder. The builders reject any other
  origin, the gate rejects it again at append, and the reader ignores any
  reminder-family event without it (a gate bypass never renders).
- `reminder` gets `data.id` minted `rem_<ulid>` when absent;
  `reminder_updated` / `reminder_cleared` without `data.reminder_id` reject
  (dead letters).
- **Reminders are NOT commitments.** Never a commitment kind, never in
  buckets/counts/chase/triage — separate types keep them out structurally.
  `data.ref` may point at a commitment for context; clearing one side never
  touches the other.
- **Recurrence lives here, not on commitments** (M decision 2026-07-09):
  `data.repeat` = `weekly` | `monthly` | `{"every_days": N}`.
- `personal: true` (default when no tracked business entity is referenced)
  renders ONLY in M-facing surfaces; `reminders.active_reminders` excludes
  personal rows unless called with `surface="m_facing"`.

The commitments/decisions/interactions the session sweep RECOVERS are written
as the existing `commitment` / `decision` / `interaction` types through
`append_event()` (dedup via `source_ref = "session:{session_id}"` in
`.source_refs.idx`) — no parallel "swept" variants of existing families.

## Balance lane (SPEC BAL1, 2026-07-19) — personal, m_facing only

| Type | Writer | Named consumers |
|---|---|---|
| `balance_nudge_suggested` | `shared/scripts/balance.py::compute_balance` — ≤1 per Sunday fire, `data.personal: true` always; payload `{tie_person_id, kind, evidence[], gap_days, baseline_days, open_slots[], proposed_action{kind, venue, draft_event_seq}}` | the balance skill's own dedupe pass (7-day per-tie window), `personal_leak.is_personal` (classifies the lane). **NO org-facing surface reads it** — the org-scoped reader (`events_io.load_events_org_scoped`) drops it by design, and Pulse's cadence math excludes it (never counts as an interaction). |
| `balance_nudge_actioned` | **`balance.record_actioned`** — the single gated writer for the apply-choices `balance` dispatch `book` confirm path (OI-3 B-1 2026-07-26; was a prose writer, which is the Gate 3 / Gate 17 defect it closed). Balance SKILL Step 4 confirm item 3 and apply-choices both name the helper and carry the same pinned type; `run_fu_pretest_pins_test` asserts the two stay equal. `data.personal: true` always; payload `{tie_person_id, source_nudge_seq, kind, personal, proposed_action{kind, venue, draft_event_seq}}` (+ optional `hold_start`) — `source_nudge_seq` is the seq of the `balance_nudge_suggested` row this confirm answers (`compute_balance`'s returned `nudge_seq`), so the linkage joins back by id rather than by tie-plus-time proximity; `draft_event_seq` links to the queued draft. **Idempotent on `(tie_person_id, source_nudge_seq)` — identity, never a clock** (OI3FIX 2026-07-26): the same card re-clicked is a permanent NO-OP that reports what is on disk and what diverged, while a NEW card writes a new linkage immediately. `DEDUPE_WINDOW_DAYS` governs the SUGGEST side only and is not read here | `personal_leak.is_personal` (type-classified via `_PERSONAL_EVENT_TYPES` — org-scoped readers drop it). **NO org-facing surface reads it** — same lane rules as `balance_nudge_suggested`. |

Hard rules: personal-lane end to end — renders only at `surface="m_facing"`;
excluded from every cadence/interaction computation; the `tie: "personal"`
marker on person records partitions the entity set (every dormancy emitter, both
secondary dormancy emitters, and relationship-moves all skip personal ties —
BAL1 D1.1). The reservation path is propose-and-confirm only: the nudge event
never books, sends, or spends; `proposed_action.draft_event_seq` is populated
by a follow-on append after the user's explicit `book` click.

## Deal lane (SPEC PIPE1, 2026-07-13)

The deal-tracking vocabulary. All five Part 1 types are written ONLY by
`shared/scripts/deal_state.py` — the single writer/closure path for `deal.*`
fields (mirrors the commitment_state doctrine: one closer, loud failures,
idempotent terminal writes). Payload shapes in `event-payloads.schema.json`.
The two Part 2 types are registered up front per the wave pattern
(registered-but-not-yet-written until the deal-signal detector ships).

| Type | Writer | Named consumers |
|---|---|---|
| `deal_created` | deal_state.create_deal / adopt_deal | pipeline-tracker (open-set + digest deltas), board-pack-assembler (§7 pipeline appendix) |
| `deal_updated` | deal_state.update_deal | pipeline-tracker (digest deltas) |
| `deal_stage_changed` | deal_state.set_stage | pipeline-tracker (days-in-stage, digest moved-list), board-pack-assembler (§7 stage table) |
| `deal_won` | deal_state.close_deal(outcome='won') | board-pack-assembler (§2 wins), value-receipt, operator-report, pipeline-tracker (won-rate tile, won-cycle median) |
| `deal_lost` | deal_state.close_deal(outcome='lost') | board-pack-assembler (§4 concerns — the "lost-deal events" it already reads), pipeline-tracker (loss-pattern readout, won-rate tile) |
| `deal_update_proposed` | `brain_proposals.propose()` on `kind: deal_update`/`deal_creation` (LB1 — written alongside the generic `brain_proposal` for the consumers named here; detector = `shared/scripts/deal_signal_detector.py`) | pipeline-tracker, cleanup |
| `deal_update_dismissed` | `brain_proposals.resolve_proposal()` on a declined deal-kind proposal (LB1 — written alongside `brain_proposal_resolved`) | proposal_ledger carries the decline cooldown (resolve_proposal appends the ledger row; propose() reads active_cooldowns) — this event is the substrate record + usage-report signal |

Hard rules:

- **`deal.stage` is never won/lost** — won/lost are the terminal `outcome`,
  written only by `close_deal` (a `deal_lost` without a valid `loss_reason`
  is rejected at the writer).
- **Observed signals never auto-flip.** Part 2 detector output is
  propose-and-confirm only (`deal_update_proposed` → user confirm →
  deal_state). User-EXPLICIT declarations ("Acme signed") mutate directly —
  the never-auto-flip rule applies to what the system infers, not what the
  user says (D6).
- **No estimation.** `data.value` appears only when user-stated/confirmed;
  absent value never becomes a guessed figure (quantify.py discipline).

## Objective lane (SPEC OBJ1, DRAFT — under review)

The standing-objectives vocabulary. All six types are written ONLY by
`shared/scripts/objective_state.py` — the single writer/closure path for
`objective.*` fields (mirrors the deal_state / commitment_state doctrine: one
closer, loud failures, idempotent terminal writes). Payload shapes in
`event-payloads.schema.json`. Directional status is never stored on the
entity — every surface derives it from these events via
`shared/scripts/objective_math.py`, branching on the objective's binding type.

| Type | Writer | Named consumers |
|---|---|---|
| `objective_created` | objective_state.create_objective (always a new thread; existing work is linked via anchor_thread_id, never kind-mutated) | objectives (the show-my-objectives readout), morning-briefing (focus line), weekly-recap (objectives section) |
| `objective_updated` | objective_state.update_objective | objectives (readout deltas, rebind trail) |
| `objective_review` | objective_state.record_review (called from the meeting-notes extraction step — the meeting-path harvest) | objectives (derived status + drift), morning-briefing (latest context line), weekly-recap |
| `objective_report` | objective_state.record_report (the weekly objectives touch in Friday Wrap) | objectives (derived status + drift), morning-briefing, weekly-recap, value-receipt (drift-flag count) |
| `objective_completed` | objective_state.complete_objective | objectives, weekly-recap, value-receipt, operator-report |
| `objective_archived` | objective_state.archive_objective | objectives, weekly-recap, usage-report |

Hard rules:

- **Status honesty follows the binding.** A directional status
  (`on_track|at_risk|off_track|blocked`) may come ONLY from a stated meeting
  review (`objective_review`), the owner's own report (`objective_report`),
  or an unambiguous activity signal (a linked deal's stage/outcome). Anything
  looser derives as "moving" or "quiet since [date]" — a fabricated
  directional status is the bug class this lane exists to prevent.
- **No parallel scanner.** The meeting-path harvest lives INSIDE the
  meeting-notes extraction pipeline (a builder in `meeting_capture.py` + one
  sub-step); relevance capture rides the existing classification envelope
  (`related_thread_ids` + `classification_confidence`) — objectives are one
  more attributable thread, corrected via the existing `reclassification`
  path. Nothing re-reads transcripts, sent mail, or sessions for objectives.
- **Topic over party.** Auto-attribution to an objective requires TOPICAL
  evidence (explicit mention / alias match, or membership in a linked
  thread or deal). Shared people or orgs are party overlap — propose-only,
  never auto-attach.
- **One weekly touch.** `objective_report` asks are batched into the single
  weekly objectives touch; no per-objective pings, no ad-hoc interruptions.
  The morning brief only surfaces (read-only per FB-20) — it never asks.

## Living Brain lane (SPEC LB1, 2026-07-14)

The unified propose → confirm → narrate → undo layer. `brain_proposal` is the
ONE generic proposal event every NEW detector writes through
`shared/scripts/brain_proposals.py::propose()`. LB2 (2026-07) migrated the
org / project / dormancy / schedule_add WRITERS onto this rail — their legacy
types are now written by nothing and adapter-read as permanent fossils
(pre-migration rows render until they resolve or age out); person and
commitment_review still write their legacy types (LB3), adapter-read into the
same queue. The `deal_update_proposed`/`deal_update_dismissed` pair
above finally gets its writer in this lane: the deal-signal detector emits
through `propose()` (a `brain_proposal` with `kind: "deal_update"` /
`"deal_creation"`), and `propose()` writes the reserved legacy type alongside
for the consumers PIPE1 already named.

| Type | Writer | Named consumers |
|---|---|---|
| `brain_proposal` | `brain_proposals.propose()` (the single entry point for every new detector; consults `proposal_ledger.active_cooldowns` + dedups on fingerprint before emitting; `tier: auto` is refused without a registered reverser in `brain_undo.REVERSERS` + an `AUTO_ALLOWED` change class) | morning-briefing (the "Needs your eyes" card), command-room-coach (Phase 2A′), weekly-recap (Phase 4 roll-up), system-health / Staff Meeting (full queue), apply-choices (`cr-brain` dispatch), cleanup (expiry sweep + card-health line) |
| `brain_proposal_resolved` | `brain_proposals.resolve_proposal()` (via apply-choices `cr-brain` handlers; also appends the decision to `proposal_ledger` so cooldown math is shared with the learning loops) | `brain_proposals.load_open_proposals` projector (tombstone), change_feed, value-receipt (confirm taps = engagement) |
| `brain_proposal_expired` | cleanup expiry sweep via `brain_proposals.expire_stale()` (silent TTL expiry — logged, never nagged) | `brain_proposals.load_open_proposals` projector (tombstone), cleanup (Monday-note card-health counts), usage-report |
| `brain_change_undone` | `brain_undo.undo_batch()` (one per reversed change; the reversal itself is the class's additive reversing event — this is the narration-trail marker) | change_feed ("undid N changes"), system-health |

Hard rules:

- **Auto-apply is a class table, not a confidence score.** `tier: "auto"` is
  legal ONLY for change classes in `brain_proposals.AUTO_ALLOWED` AND with a
  reverser registered in `brain_undo.REVERSERS` — `propose()` raises
  otherwise. Identity- and money-shaped changes are always `confirm`
  (Bug #92 / PIPE1 D9). The one R1 exception: person/org creation from a
  STRUCTURED CONNECTOR FACT (full name + address from mail/calendar, zero
  same-name collision, past the noise gate) is `auto` — additive only,
  archive reverser registered, narrated in the change feed. Prose-inferred
  identities stay `confirm`; merges stay `confirm` permanently.
- **Undo is additive.** Reversers append reversing events
  (`commitment_reopened`, `chat_dismissal_cleared`, a status→archived
  `person_updated`/`org_updated`) — never edit or delete prior events.
- **Narration is never the enforcement artifact.** `change_feed.py` is a
  READER over audit events + these tombstones; enforcement binds to the
  audit events themselves (the reconcile-sent doctrine).
- **Anti-fatigue is contract:** `DAILY_CONFIRM_CAP = 5` on daily surfaces,
  max 2 slots per detector per render, TTL default 14d with silent expiry,
  declined ⇒ 60d fingerprint cooldown via the shared ledger, and a proposal
  whose `action_tuples` map to no registered verb is rejected at `propose()`
  (no-consumer proposals never enter the queue).

## Entity history & lineage lane (SPEC HIST1, 2026-07-18)

The per-person / per-company history vocabulary. Lineage rides the change the
user already confirmed: the two `*_changed` types are emitted by
`people_writer.update_person` at the moment a confirm-gated role/company
change is applied (the before/after snapshot is already in hand at the
`person_updated` emit site) — no new gate, no new prompt; the head field
still updates, the prior value is preserved as history. Facts are additive,
sourced events written by the two `record_*_fact` writers — they NEVER touch
the entity record (D1: history is events + a renderer, never a frozen
field). Consumer column lists ONLY the consumers Part 1 actually wires (no
consumer-inflation); future consumers (dormant-customer-scan, usage-report,
the change-feed spec) are added when they read. Payload shapes in
`event-payloads.schema.json`.

| Type | Writer | Named consumers |
|---|---|---|
| `person_role_changed` | people_writer.update_person (auto, at the before/after diff site; suppressed by `suppress_lineage=True` for migration sets) | render_person_history, render_org_history, call-prep (relationship timeline) |
| `person_org_changed` | people_writer.update_person (auto, same site) | render_person_history, render_org_history (joined/left movement block), call-prep |
| `person_fact_observed` | people_writer.record_person_fact (explicit user statement, confirmed proposal, or — Part 2 — the `entity_fact_structured` auto tier via entity_signal_detector.apply_structured_facts, batch-stamped for undo; auto is limited to preference/contact/personal, S2) | render_person_history, call-prep, change_feed (auto-noted count line) |
| `org_fact_observed` | org_writer.record_org_fact (explicit user statement, confirmed proposal, or the Part 2 structured auto tier — same S2 category limits) | render_org_history, board-pack-assembler (§1/§4 company context), change_feed (auto-noted count line) |
| `entity_fact_retracted` | brain_undo `entity_fact_structured` reverser (Part 2 — landed in the SAME commit as the AUTO_ALLOWED entry, closing the wave-pattern registration) | render_person_history, render_org_history (suppress the retracted fact) |

Hard rules:

- **`source_ref` is never null.** Required on the fact events; on the auto
  `*_changed` events, when no connector ref is in hand the writer synthesizes
  `update:<source_skill>:<seq of the triggering person_updated event>` so the
  future change feed (HIST1 D10) always has a handle.
- **No lineage from backfill churn.** A `*_changed` event is emitted only
  when before != after AND both sides are non-empty — filling an empty
  role/org for the first time is enrichment, not a move. Bulk
  re-attributions pass `suppress_lineage=True` and emit nothing.
- **Facts never mutate records.** `record_person_fact` /
  `record_org_fact` append events only; the entity record is untouched.

## People identity lane (SPEC PID1, 2026-07-19)

The three-tier identity reconciler's vocabulary (auto-add on hard
corroboration via the existing `person_org_creation_structured_fact` rail;
identity-clustered confirm rows; merge-propose never auto-merge). Payload
shapes in `event-payloads.schema.json`.

| Type | Writer | Named consumers |
|---|---|---|
| `unidentified_attendee_observed` | `meeting_capture.build_unidentified_attendee_event` (meeting-notes Step 5f / past-meetings Phase 4.5b for unnamed speakers — NEVER a person proposal; the proposal builder raises on empty names by design) + `identity_reconcile.run_identity_reconcile` (backfill conversion of legacy no-name rows) | `identity_reconcile.load_open_annotations` / `count_open_annotations` (the staff meeting's ONE count line — §0-4: otherwise fully silent, never a queue row) |
| `identity_reconcile_run` | `receipts.log_receipt` from `identity_reconcile.run_identity_reconcile(apply=True)` — ONE per pass (Sunday `identity-reconcile` maintenance job + the M-fired one-time backfill) | maintenance_dispatcher due-ness rule, `change_feed.changes_since` (the D6 `people_added` / `people_linked` CHANGED lines — counts from what was WRITTEN, never the plan), `identity_reconcile.load_open_annotations` (`annotations_resolved` fold) |
| `contact_captured` | `contact_capture.capture_contacts` (SPEC CONTACT1). One row per ADJUDICATION or ATTEMPT on an address, discriminated by `data.outcome`: `created` (carries `brain_batch_id` + `brain_change_class: person_org_creation_structured_fact` — the EXISTING R1 class, no new class, no new reverser — plus `person_id`), `needs_confirm` (a same-name collision was asked about once; deliberately carries NO undo stamp, since it created nothing to reverse), `deferred` / `gave_up` (attempt bookkeeping with `attempts` + `reason`). Every row carries the `contact:<address>` fingerprint and the message the identity was observed in. | `contact_capture.already_captured` / `_captured_fingerprints` (THE idempotency ledger — append-only, so it survives an undo and a cursor replay can never re-create an archived record; attempt rows are excluded, because a write that failed is not an answer), `contact_capture.stuck_attempts` (the deferral bound — a permanent failure stops freezing the cursor after three fires), `brain_undo.recent_auto_batches` + `_changes_for_brain_batch` (the undo listing and the archive reverser, both keyed on the batch stamp), provenance trail for the auto-created record |
| `lifecycle_run` | `receipts.log_receipt` from `lifecycle_pass.run_lifecycle_pass(apply=True)` — ONE per pass (the Sunday `lifecycle` maintenance job; SPEC LIFECYCLE1, the fold that replaced Pulse's Phase 4). A dry run writes NOTHING, so the job stays due. | `maintenance_dispatcher` due-ness rule (this job's own success validator — a job vouches for itself), audit trail for the silent active->dormant / dormant->archived / revive transitions |
| `note` (pre-registry legacy type; new writer registered MLK1 2026-07-21) | `orphan_note.reroute_orphan_note` (apply-choices orphan-note re-route — a typed widget note with no action selected lands as a note on its resolved person/thread, `data.via: "orphan_note_capture"`; DECLINED with nothing written when no target resolves; IDEMPOTENT on `(target, source_event_seq, text)` since DOGFIX1 2026-07-27 — a re-dispatched apply-choices payload returns `already_noted` and writes nothing, and the dedupe scan is scoped to `data.via == "orphan_note_capture"` so the legacy writers below can never swallow a capture) + legacy writers (session-backfill / session-sweep / historical-backfill / intel-intake prose paths) | `capture_gate` (substantive-candidate types), `entity_signal_detector` / `deal_signal_detector` (signal scans), `session_sweep.SWEEPABLE_TYPES`, update-bridge migration gate (ingest-signal count) |

Hard rules:

- **No merge is ever automatic.** `person_merge` / `person_link` proposals
  ride the bp rail at tier `confirm`; `merge_person_into` has NO registered
  reverser and `person_merge` is never in `AUTO_ALLOWED`.
- **Caps spill narrated** (§0-3): auto-add ≤15/backfill batch, ≤10/week
  steady-state; merge-propose ≤10/Sunday render — overflow is counted in
  the receipt (`spilled`), never silently dropped.
- **D8 fingerprint tombstones**: a `seq: null` proposal resolves via
  `data.proposal_fingerprint` (`confirm_flow.compute_proposal_fingerprint`)
  on the existing `person_proposal_resolved` / `person_proposal_reopened`
  types — int-seq matching stays untouched and preferred.
  A `history[]` / `career[]` field on the entity is the forbidden shape
  (`FORBIDDEN_PERSON_FIELDS` doctrine).
- **The auto fact tier is structured-and-non-identity ONLY (Part 2,
  landed).** Money and identity stay confirm (Bug #92); the
  structured-connector `entity_fact_structured` auto class and its
  `entity_fact_retracted` reverser shipped together (brain_proposals.
  AUTO_ALLOWED + brain_undo.REVERSERS). Auto categories:
  preference/contact/personal — `role`/`company_news` facts ride the
  confirm rail even from a structured source (S2). Auto facts are applied
  directly through the fact writers with `brain_batch_id` stamps and
  narrated in the brief's CHANGED line with a standing `undo` (the PID1
  applied-then-narrated posture) — never an open auto proposal.

## Chart lane (SPEC OUT3B, 2026-07-19)

One on-demand chart answer per ask. Written ONLY by the `chart-on-demand`
skill. Payload shape in `event-payloads.schema.json`.

| Type | Writer | Named consumers |
|---|---|---|
| `chart_render` | chart-on-demand skill (once per ask) | usage-report / insight-generator (mine what gets charted — the ask corpus is empty at ship), check-deliverables / value-receipt / cleanup (see the persisted page via `data.artifact`) |

Hard rules:

- **Refusal logs too (D4).** An ask with no substrate answer emits
  `chart_render` with `refused: true` + `data.reason` (naming the closest
  catalog entry). A refusal is text, not a page — `artifact` is absent. With
  zero recorded chart asks today, refusal receipts are how the closed series
  catalog learns what the CEO actually wants charted.
- **Closed catalog only.** `data.catalog_id` is one of the enumerated series
  (`value_trend` / `pipeline_mix` / `pipeline_by_org`); numbers come from the
  owning helper, never invented (refusal over fabrication).
- **One chart per event.** Compound asks split into sequential asks, one
  `chart_render` each.

## Coach lane (SPEC COACH1 §4.5, 2026-07-24) — coach-pack repos only

The Business Coach Pack's whole vocabulary, **registered in one pass up front**
per the wave pattern above: every type below is in the enum from Phase 1, so
each later phase's source-of-truth check passes without re-opening it. A type
whose writer names a phase later than the one that shipped is
registered-but-not-yet-written — expected until its phase lands.

The pack ships in `packs/coach/` and reaches only the client repos that name
`"packs": ["coach"]` in `_chalette/clients.json`; the types are core-owned
because the enum is core-owned. On a non-coach workspace nothing writes them —
same posture as any other registered-but-unwritten type, no migration, no
reader change.

Derived numbers never appear as stored entity fields: every count, window,
tally, roster occupancy, and flag the pack surfaces is computed from these
events by `shared/scripts/coach_state.py` (the `brief_state.py` / `eos_state.py`
discipline). All writes go through `append_event()` / `atomic_append_jsonl`
(the A1 lock contract) — no exceptions, no second append path.

| Type | Writer (phase) | Named consumers |
|---|---|---|
| `coaching_engagement_started` | coach-intake (Phase 2) — one per coaching relationship opened; carries the thread id, cadence, and stated term | coach-renewal (term watch anchor), coach-practice-review (engagement count + tenure) |
| `engagement_baseline_set` | coach-intake (Phase 2) — the arc's zero point: where the client stood at session one | coach-session-prep (the "since the baseline" read), coach-practice-review (movement against baseline) |
| `session_captured` | coach-session-capture (Phase 2) — one per 1:1 session turned into the coach's template; the arc's spine | coach-session-prep (last session + what's open), coach-billing (a real session is a billable unit), coach-practice-review |
| `session_prep_generated` | coach-session-prep (Phase 2) — one receipt per prep pack built | usage-report (did the scheduled prep actually fire) |
| `arc_pattern_flagged` | coach-session-capture (Phase 2) — a long-arc observation, e.g. a commitment promised twice without motion | coach-session-prep (surfaces the pattern in the next prep, which is the whole point of the arc) |
| `cohort_session_captured` | coach-group-pack (Phase 3) — one per GROUP session, with per-member items inside the record; never one event per attendee | coach-session-prep (cohort prep), coach-billing (seat-level units for the period) |
| `cohort_member_added` | coach-group-pack, enable-coach (Phase 2/3) — a seat joins; `{cohort_thread_id, person_id, joined_at}` | coach-billing (the seat enters the period), coach-renewal (roster size at renewal) |
| `cohort_member_departed` | coach-group-pack, enable-coach (Phase 2/3) — a seat leaves; a departure with no date is rejected at the writer (it can't be billed to a period boundary) | coach-billing (the seat leaves the period), coach-renewal |
| `material_surfaced` | coach-toolbox (Phase 5) — a piece of the coach's own material was surfaced into a session or prep; names the material, NEVER another client's situation (§12 firewall) | coach-practice-review (which material actually gets used) |
| `billable_session_logged` | coach-billing (Phase 4) — a unit CONFIRMED by the coach, including informal contact that never hit the calendar. Never written from a silent inference | coach-billing itself (the period tally), coach-practice-review (practice economics) |
| `invoice_drafted` | coach-billing (Phase 4) — the assembled draft, one per payer per period, itemized per person and per cohort seat | coach-practice-review, value-receipt |
| `invoice_sent` | coach-billing (Phase 4) — written only after a per-invoice explicit confirm; one confirm, one invoice | coach-practice-review, value-receipt |
| `renewal_window_opened` | coach-renewal (Phase 6) — a real `term_end` / `renewal_date` came into range; not a dormancy inference | morning-briefing (it belongs in the day), coach-practice-review |
| `referral_moment_flagged` | coach-renewal (Phase 6) — a win the coach could ask a referral off | relationship-moves (it owns the ask), coach-practice-review |
| `member_artifact_delivered` | coach-member-pack (Phase 3) — a member-facing artifact went out in the coach's name (§9, the channel) | coach-practice-review, value-receipt |

Hard rules:

- **Draft, then send (M ruling 7).** `invoice_sent` may only follow an explicit
  per-invoice confirm. There is no batch or scheduled send path, and
  `workspace.coach.billing.invoice_posture` is single-valued (`draft_then_send`)
  so one cannot be selected into existence.
- **Never a silent billable.** `billable_session_logged` records the coach's
  confirmation. Candidate unbilled contact is surfaced as a proposal through the
  action widget and is dismissible; the proposal is not the event.
- **Group means group.** A cohort session is ONE `cohort_session_captured` with
  per-member items in the payload — never one event, and never one recap email,
  per attendee. Eight personalized sends read as automation and cost the coach
  credibility.
- **Email stays email-writer's monopoly (Rule 30).** Every type here is a
  content/state event; none of them sends anything. The pack produces content
  and delegates the draft.
- **Cross-client firewall (§12).** `material_surfaced` names material. A payload
  that identifies another client's situation is the defect this lane's review
  looks for first.
- **No `coach_session` reuse.** The pre-existing `coach_session` type belongs to
  `command-room-coach` (the skill that coaches the USER on Command Room) and is
  unrelated. Use `session_captured` / `cohort_session_captured`.

## Commitment-family append contract (gate-enforced)

- `type: commitment` — `data.id` is minted as `cmt_<ulid>` at write time when
  absent (never synthesized-only); `data.kind` is required, one of
  `promise | task | scheduling | agenda` (ratified 2026-07-01). A missing
  kind is stamped `promise` (the behavior-preserving default) until Phase 2
  Stage D migrates every producer to emit kind explicitly; an invalid kind is
  rejected.
  **CTS1 §5 consistency check (warn-level, NEW writes only — never rejects):**
  the gate warns to stderr when `kind: task` carries a counterparty signal
  (a task is self-owed by definition — someone waiting means it's a promise)
  or when `kind: promise` carries NO counterparty signal and no
  `pending_review` (it will render "counterparty unresolved" on My Plate —
  link the counterparty at capture when known). The signal test goes through
  `commitment_parties` (the MC1 ids/names union) plus `requester_*` /
  `owner_external`. ~49 live rows predate this check and violate the promise
  half — they converge via the CTS1 §8.2 drip/batch fixup, never via a
  substrate-wide warning sweep. CTS1 introduces NO new event types: the two
  surfaces (Waiting On / My Plate) are read-side filters
  (`shared/scripts/surface_split.py`) over the projected open set, and every
  kind change rides the existing additive `commitment_reclassified` marker.
- `type: commitment_resolved` — MUST carry a readable id in one of
  `data.commitment_id` (preferred), `data.id`, `data.target_id` (legacy),
  `data.commitment_seq`, `data.source_event_seq`. An id-less closure is
  rejected at append time (EventGateError): it would be a dead letter — 291
  of them existed in the live substrate when this gate shipped. As of Phase 2
  Stage C the seq aliases are also READ: `load_open_commitments` and
  `close_commitment`'s idempotency both resolve `commitment_seq` /
  `source_event_seq` → the commitment at that seq (F3 amnesty; ~252 historic
  dead letters recovered read-side, no history rewrite).
- **Writing closures (Phase 2 Stage B):** `commitment_resolved` is written
  ONLY through `commitment_state.close_commitment()` — the single closure
  path (legacy-id normalization via seq lookup, loud `CommitmentIdError` on
  no match, idempotency over the full resolved-id set, `pending_review`
  floor, `data.resolution` in done | dropped | superseded).
  `cru_match.build_commitment_resolved_event` is a legacy shape helper —
  construction-only, never build-and-append in new code.
- `commitment_superseded` — the MERGE closer (v4.6.0 C4) and the SPLIT closer
  (v4.6.0 S4). Written ONLY through
  `commitment_state.supersede_commitment()` (merge: closes a duplicate in
  favor of a survivor) or `commitment_state.split_commitment()` (split: closes
  the original in favor of its N parts — `data.split_into` lists the child
  commitment ids, `data.superseded_by` names the first child, evidence says
  "split into …"; each child `commitment` event carries
  `data.source_event_seq` → the original + `data.split_from`, and the
  survivor-provenance fold below is SKIPPED when `split_into` is present).
  For merges: `data.commitment_id` + `data.commitment_seq` reference the
  superseded item through the standard closer chains; `data.superseded_by`
  names the survivor; `data.merged_source_refs` is the provenance union.
  **Named consumers:** `load_open_commitments` (closer since v3.14.5 + the C4
  survivor-provenance fold — `data.merged_source_refs`/`data.merged_from` on
  the survivor's in-memory copy), `close_commitment`'s idempotency index,
  `email_outcomes`, `audit_closure_integrity`. Suspected duplicates are
  FLAGGED at capture by `shared/scripts/commitment_dedup.py` (hooked inside
  the single append path, same doctrine as the gate): the new commitment
  lands with `data.pending_review: true` + `data.suspected_duplicate_of` —
  never silently dropped, never silently merged; the confirm flow (W4b) or
  the "merge those two" chat phrase (commitment-triage) adjudicates.
- `commitment_observed` — the set-aside tier (v4.6.1 W4c, the volume fix):
  a commitment-shaped item the relevance gate kept on file WITHOUT opening —
  third-party↔third-party, or attribution the extraction couldn't confidently
  resolve (amber is silent by default). Deliberately a SEPARATE type from
  `commitment`, not a tier field on it: every open-set reader filters
  `type == "commitment"`, so observed items are excluded from counts, triage,
  the confirm section, chase, and CRU candidacy BY CONSTRUCTION — the same
  doctrine that keeps reminders out (W4a). **Writers:** every capture leg via
  `capture_gate.build_observed_event` / `observed_from_commitment_event`
  (session_sweep routes automatically; scan-for-commitments' meeting/Slack
  legs per its Step 3.5). The builder REFUSES items carrying a due date or a
  money amount — the asymmetric caution rail opens those instead, in every
  mode. `data.id` is deterministic `obs_<sha256[:12]>(source_ref|title)`
  (re-scan idempotent). **Named consumers:** `capture_gate.observed_counts`
  (the weekly cleanup "N items set aside — review" line),
  `capture_gate.prep_context_observed` (call-prep context with a track-it
  affordance), `capture_gate.find_corroborations` / `promote_observed`
  (promotion appends a REAL `commitment` with `data.pending_review: true` +
  `data.promoted_from` → the confirm flow picks it up by data contract),
  transcript-search (observed items are part of the searchable record).
  Capture policy (modes + per-org overrides) is SCL1 directives under
  `scan-for-commitments`; full contract in `COMMITMENT_SCHEMA.md`
  § Observed tier.
- `commitment_update` is drift; the gate rewrites it to `commitment_updated`.
- `commitment_updated` — writers: the Commitments orchestrator `push to [date]`
  verb (`data: {commitment_id, new_due, reason}`), the CRU schedule-shift
  path (`cru_match.build_commitment_updated_event`,
  `data: {commitment_id, change_summary, evidence}`), and the S4 `fix wording`
  verb (`commitment_state.edit_commitment_wording`,
  `data: {commitment_id, new_title?, new_summary?, edited_by}`). **Named
  consumer (Phase 2 Stage A + v4.6.0 S4):** the commitment-state projector —
  `cru_match.load_open_commitments` folds the latest `data.new_due` (variants
  `due` / `due_date` accepted) into the returned commitment's effective
  `data.due`, so a deferred item stops rendering overdue, and folds the latest
  `data.new_title` / `data.new_summary` (each field independently, newest
  wins) into the projected item's wording — the original text stays in
  history, append-only (mis-extracted summaries were uncorrectable before
  S4). `change_summary` is informational prose describing WHAT changed
  (schedule shifts) and is deliberately NEVER folded into wording. Updates
  that carry none of the folded fields affect no fold.
- `inbound_reconcile` (REPLYCLOSE, 2026-07) — **writer:**
  `reconcile_inbound_commitments.reconcile_and_receipt`'s inbound twin,
  `reconcile_inbound_and_receipt`, which is the ONE orchestrator behind both
  inbound fires (inbox-triage's Phase 5.5 and the Waiting-On backstop's
  Phase 2.6). One audit row per fire, always — a 0-scan run writes one too.
  `data: {kind, status, fired_via, batch_id, inbound_scanned_count, n_closed,
  n_pending, n_updated, n_partial_receipts, signal_fields, coverage}`.
  `status` is `complete` on a real read and **`blocked`** when the inbound read
  could not happen at all (TRAINFIX F-4 — the inbound mirror of MAILSEAM item
  8); a blocked row adds `blocked_reason` in plain language, closes nothing and
  queues no confirm. Before it, a fire that read NOTHING wrote the same clean
  `inbound_scanned_count: 0` row as a fire that read everything and found
  nothing, and the validator passed it.
  **Named consumer:** `reconcile_inbound_commitments.validate_inbound_reconcile_ran`,
  which both call sites run as their mandatory self-validation — a narrated
  "checked your inbox" with no row here is a fire that did not happen.
  `signal_fields` answers "did the reply checks RUN" (presence of the thread /
  attachment fields on the fetch, counted separately from their truth) and
  `coverage` answers "how much of the open set can this rail reach at all"
  (items with no resolvable owner can never be closed by a reply, and the
  receipt says so rather than implying full coverage). This is an AUDIT TRACE,
  not a task receipt: it carries no `task_id`, `receipts.count_runs` never
  sees it, and the host task still writes its own `pack_run`.
- `backlog_sweep` (SWEEPBACK, 2026-07-30) — **writer:**
  `commitment_backlog_sweep.scan`, the ON-DEMAND historical backlog sweep
  (`commitment-backlog-sweep` skill; never scheduled, registers no task). ONE
  audit row per run, ALWAYS — including a dry run, whose row is the only thing
  that run writes. `data: {kind, status, blocked_reason, dry_run, batch_id,
  window_start, window_days, age_out_days, mail_provider, item_cap,
  n_open_total, n_scanned, n_reachable_total, has_more, resume_after,
  n_auto_closed, n_proposed, n_merge_groups, n_age_out, auto_closed, coverage,
  signal_fields}`. `status` is `complete` or `blocked` (the historical mail read
  could not happen), same discipline as both mail rails.
  **Named consumers:** `commitment_backlog_sweep.validate_sweep_ran`, the
  ungameable half — a narrated "swept your backlog" with no row here is a sweep
  that did not happen, and a `blocked` row is refused with its reason; and
  `commitment_backlog_sweep.last_scan`, which reads `resume_after` off the newest
  row so a capped run resumes where it stopped instead of re-reading its own head
  (the CATCHUP1 precedent — the resume point lives on the receipt, not in a side
  file). `coverage` states the population the sweep could NOT reach and why
  (items with no counterparty, items with no mail anchor, the thread-anchored
  count it actually found, the meeting-sourced pile), because a receipt claiming
  full coverage of the open set would be lying by omission. This is an AUDIT
  TRACE, not a task receipt: no `task_id`, `receipts.count_runs` never sees it,
  and there is no host task — the sweep only ever runs because a human asked.
  The closures a sweep applies are ordinary `commitment_resolved` /
  `commitment_superseded` rows stamped `brain_batch_id: swb_<UTC>-<8 hex>` +
  `brain_change_class`, so `undo` lists and reverses a sweep with the reversers
  that already exist.

### `seq_repaired` (BUG-8330 item 7c, 2026-08-10)

| type | writer | readers |
|------|--------|---------|
| `seq_repaired` | `seq_health.detect_and_mark(apply=True)` — the ONLY writer (cleanup's weekly pass) | `seq_health.detect_and_mark` (its own dedup memory — an already-marked duplicate seq is not re-reported), cleanup's Monday note + system-health's report line (render the counts) |

One additive marker per NEWLY-detected duplicated seq (`data.duplicate_seq`,
`n_occurrences`, `event_types`). The events holding the duplicate stay exactly
as written — history is never rewritten. Post-A1 the appender allocates seq
inside the writer lock, so a NEW marker indicates a real writer bug worth
eyes, which is exactly what the Monday note surfaces.

## Receipt contract (v4.5.2 R1)

Scheduled-task run receipts (`pack_run`, `sent_reconcile`, `chat_reconcile`,
`session_sweep_run`, `cleanup_run`, `operator_report_generated`, ...) have their
own schema layer on
top of this registry: canonical task_id spellings, the `late_tier` field name,
`fired_via`, `machine`, and the one shared writer/reader. See
`shared/RECEIPT_CONTRACT.md` + `shared/scripts/receipts.py`. Writers call
`log_receipt()` — never hand-rolled receipt JSON; readers go through
`iter_receipts()` / `count_runs()` — never per-reader matchers.

### `chat_reconcile` (SPEC CHATSCAN1 §B, 2026-08-08)

| type | writer | readers |
|------|--------|---------|
| `chat_reconcile` | `chat_reconcile._log_receipt` -> `receipts.log_receipt` (task_id `reconcile-chat`) — the ONLY writer | `chat_reconcile.validate_chat_reconcile_ran` (the leg's own success validator), `maintenance_dispatcher` due-ness via `receipts.last_receipt_times`, `task_watchdog.check_maintenance_jobs` |

The chat closure leg's per-fire receipt. Deliberately a DIFFERENT type from
`sent_reconcile` even though the two legs run in the same fire and carry the
same shape: `validate_reconcile_ran` reads "the latest `sent_reconcile`
event", so a chat leg writing that type would have satisfied the mail leg's
validator, and a mail leg that never fired would have read as healthy. The two
legs must not be able to vouch for each other.

Written on EVERY terminal state, which is the whole point:
`status: "skipped"` (no chat backend declared — the leg correctly did nothing,
and without a receipt that is indistinguishable from a sweep that found
nothing), `status: "blocked"` (a declared backend whose read could not happen —
the cursor does not advance and the validator refuses it), `status: "degraded"`
(a real run on the partial per-conversation path — carries `scan_mode` and a
plain-language `coverage_note` so no surface claims a full reconcile), and
`status: "complete"`.

The closures it produces are ordinary `commitment_resolved` rows stamped
`resolved_by: "chat_reconcile"`, each carrying BOTH spellings of one pointer:
`data.source_ref` (the canonical `<provider>:<room>:<message id>` string every
existing reader and the dedup index already understand) and
`data.chat_source_ref` (the structured `{provider, kind, chat_or_channel_id,
message_id, ts}` — provider and kind explicit, because Slack addresses a
message as channel+ts and Teams as chat+message-id, and a reader must know
which shape it holds). `connector_adapters.chat.pointer_fields` emits both
together so a writer cannot produce one half without the other. No pointer, no
close.

## Read-side timestamp contract

Writers emit `ts` only (auto-stamped inside the writer lock). History is
additive-only forever — the 156 `timestamp` and 17 `date` spellings already
in live substrates are never rewritten. Every reader that orders or filters
events by time goes through `shared/scripts/event_time.py`
(`event_time` / `event_dt`), which resolves `ts` → `timestamp` → `date` in
priority order.

## Decision-review lane (WALKFIX1 FR-2, 2026-08-10) — ⚠ M-STRIKEABLE

- `decision_supersede_proposed` — **writer:**
  `decision_match.build_decision_supersede_proposal_event`, appended by the
  past-meetings Phase 4.6.b decision-CRU pass in place of the
  `decision_superseded` it used to auto-write. `data: {decision_id,
  proposed_action, evidence, status, score, title}`. `proposed_action` is
  always `decision_superseded`; `status` is always `proposed`.
  **Named consumer:** `render_decision_log._categorize_decisions` →
  `proposals_map`, which `_format_decision_line` renders as a
  `[SUPERSEDE PROPOSED]` note ON THE DECISION'S OWN LINE — the person who owns
  the decision adjudicates it where the decision lives, rather than the fire
  closing it for them.

  **This event NEVER changes a decision's status.** That is the whole point:
  one 2026-08-10 fire read two transcripts and auto-wrote nineteen
  `decision_superseded` events, four of four sampled being plainly wrong (a
  client onboarding call recorded as reversing an unrelated internal meeting
  time, an office-lease decision, another person's login preference and
  another client's video platform). The matcher scores a whole transcript
  against a short title with an overlap coefficient and ANDs it with a
  whole-transcript reversal boolean, with no locality requirement — a signal
  strong enough to propose on and far too weak to write closures on. The full
  argument and the complete strike set live at `RECOMMEND_ONLY_SUPERSEDES` in
  `shared/scripts/decision_match.py`.
