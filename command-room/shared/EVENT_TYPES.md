# Event-Type Registry (Phase 1 Foundation, 2026-07)

**Enum home (decided once, final):** the event-type enum lives in
`shared/data-schemas/events.schema.json` — nowhere else. It is machine-read by
three enforcers, so there is no second copy to drift:

1. **Write time** — `shared/scripts/event_gate.py` (the `append_event()`
   gatekeeper, wired inside `atomic_append_jsonl`'s events.jsonl branch)
   validates every appended event's `type` against the enum via
   `shared/scripts/event_types.py`. Strict (reject) through `append_event()`;
   warn-on-stderr through the legacy `atomic_append_jsonl` entry during
   burn-in.
2. **Static guard** — `tests/run_source_of_truth_test.py` Check 4 fails the
   battery when any documented `"type": "<name>"` literal in skill prose or
   shared scripts is absent from the enum.
3. **Workspace audit** — weekly-audit validates live events against the same
   schema.

## Registering a new event type

1. Add the name to the `type` enum in `shared/data-schemas/events.schema.json`.
2. Add a row to the table below naming the **writer** and at least one
   **named consumer** (no consumer-less writes — Writes-checklist item 5,
   enforced at review). If the payload has a required shape, also add it to
   `shared/data-schemas/event-payloads.schema.json`.
3. Never write a type that isn't registered; never spell-variant an existing
   type (the gate normalizes known drift — e.g. `commitment_update` →
   `commitment_updated` — but new drift is a defect).

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
| `pulse_run` | Pulse orchestrator (Phase 3/6 quick win B) | insight-generator (cadence baseline), usage-report, value-receipt |
| `triage_feedback` | apply-choices, on every inbox action at dispatch (Phase 6, Loop 1) — `{sender, domain, bucket_assigned, action_taken, draft_offered}` via `triage_feedback.build_triage_feedback_event` | insight-generator Pass 13 (sender-priority proposals), usage-report |
| `prep_feedback` | orchestrator-past-meetings grades the prep brief against the transcript after meeting-notes runs (Phase 6, Loop 3) — `{meeting_id, meeting_type, sections_hit, sections_rendered, sections_missed, unpredicted_topics}` via `prep_grading.build_prep_feedback_event` | insight-generator Pass 15 (section-weight proposals), value-receipt |
| `prep_brief` | BOTH prep paths — orchestrator-upcoming-meetings Phase 4 auto-prep AND call-prep on-demand 'prep me' — one per Call_Prep brief saved, via `receipts.log_prep_receipt` ONLY (v4.5.2 S1; F-29/F-29b) — `{meeting_id, slug, artifact, generated_by, fired_via, refreshed}`. NOT a task-run receipt: five briefs in one fire are five prep_brief events and ONE pack_run | morning-briefing no-prep detection (`receipts.prep_exists_for_meeting` — the "no prep" flag may only render when NO receipt exists for that meeting id), upcoming-meetings/call-prep refresh-in-place check, value-receipt |
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
| `commitment_reassigned` | `commitment_state.reassign_commitment` (v4.6.0 S4 — the `reassign to [name]` verb + the "that's actually [name]'s" chat phrase; W4b's Theirs→[name] confirm verb dispatches it with `confirmed: true`) | the projector (`load_open_commitments` reassignment fold — latest event wins; unconfirmed reassignments stamp `pending_review` so the item sits in the unconfirmed bucket and never enters chase), `commitment_counts` direction buckets, commitment-triage, the daily Commitments chat |
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

## Living Brain lane (SPEC LB1, 2026-07-14)

The unified propose → confirm → narrate → undo layer. `brain_proposal` is the
ONE generic proposal event every NEW detector writes through
`shared/scripts/brain_proposals.py::propose()` — the 8 legacy proposal
families keep their own types and are adapter-read into the same queue
(migration is LB2). The `deal_update_proposed`/`deal_update_dismissed` pair
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

## Commitment-family append contract (gate-enforced)

- `type: commitment` — `data.id` is minted as `cmt_<ulid>` at write time when
  absent (never synthesized-only); `data.kind` is required, one of
  `promise | task | scheduling | agenda` (ratified 2026-07-01). A missing
  kind is stamped `promise` (the behavior-preserving default) until Phase 2
  Stage D migrates every producer to emit kind explicitly; an invalid kind is
  rejected.
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

## Receipt contract (v4.5.2 R1)

Scheduled-task run receipts (`pack_run`, `sent_reconcile`, `session_sweep_run`,
`cleanup_run`, `operator_report_generated`, ...) have their own schema layer on
top of this registry: canonical task_id spellings, the `late_tier` field name,
`fired_via`, `machine`, and the one shared writer/reader. See
`shared/RECEIPT_CONTRACT.md` + `shared/scripts/receipts.py`. Writers call
`log_receipt()` — never hand-rolled receipt JSON; readers go through
`iter_receipts()` / `count_runs()` — never per-reader matchers.

## Read-side timestamp contract

Writers emit `ts` only (auto-stamped inside the writer lock). History is
additive-only forever — the 156 `timestamp` and 17 `date` spellings already
in live substrates are never rewritten. Every reader that orders or filters
events by time goes through `shared/scripts/event_time.py`
(`event_time` / `event_dt`), which resolves `ts` → `timestamp` → `date` in
priority order.
