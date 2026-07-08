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
| `late_fire` | scheduled-task orchestrators via lateness tiers (Phase 3, R4) | cleanup (chronic-lateness detection), insight-generator (better-default-time proposals) |
| `pulse_run` | Pulse orchestrator (Phase 3/6 quick win B) | insight-generator (cadence baseline), usage-report, value-receipt |
| `triage_feedback` | apply-choices, on every inbox action at dispatch (Phase 6, Loop 1) — `{sender, domain, bucket_assigned, action_taken, draft_offered}` via `triage_feedback.build_triage_feedback_event` | insight-generator Pass 13 (sender-priority proposals), usage-report |
| `prep_feedback` | orchestrator-past-meetings grades the prep brief against the transcript after meeting-notes runs (Phase 6, Loop 3) — `{meeting_id, meeting_type, sections_hit, sections_rendered, sections_missed, unpredicted_topics}` via `prep_grading.build_prep_feedback_event` | insight-generator Pass 15 (section-weight proposals), value-receipt |
| `prep_weight_proposal` | insight-generator Pass 15 (Phase 6, Loop 3) — one per user decision on a proposed call-prep section-weight change; the applied weight lands in the call-prep skill config (`_hq/data/skill_config/call-prep.json`) | call-prep (reads section weights before rendering), usage-report |
| `extraction_hint_proposal` | insight-generator Loop 5 pass (Phase 6, Round 3) — one per user decision on a proposed extraction hint; the applied hint appends to `_hq/data/extraction-hints.md` | meeting-notes (extraction prompt), cru_match (resolution language), usage-report |
| `sender_priority_proposal` | insight-generator Pass 13 (Phase 6, Loop 1) — one per user decision on a proposed sender/domain priority rule (`user_action` applied/edited/declined/skipped, `fingerprint`); the applied rule lands in `_hq/data/sender-priority-rules.json` | insight-generator (60-day fingerprint cooldown via `proposal_ledger`), usage-report |
| `surface_preference_proposal` | insight-generator Pass 14 (Phase 6, Loop 2) — one per user decision on a proposed suppression; the applied rule lands in `_hq/data/surface-preferences.json` | insight-generator (60-day cooldown), every widget orchestrator (reads the store to filter), usage-report |
| `confidence_override_proposal` | insight-generator Loop 4 calibration (Phase 6, Round 2) — one per user decision on a proposed match-score threshold change; the applied value lands in `_hq/data/confidence-overrides.json` | `confidence.py` accessors (read the override), cru_match (thresholds), usage-report |
| `chase_policy_proposal` | insight-generator Pass 7b propose-and-apply (Phase 6, Loop 6) — one per user decision on a per-relationship-type chase window; the applied rule lands in `_hq/data/chase-policy.json` | orchestrator-commitments (chase cadence), email-writer (follow-up drafts), usage-report |
| `commitment_noise_proposal` | insight-generator S3 rider (Phase 6, Round 2) — one per user decision on a proposed never-track rule; the applied rule appends to `_hq/config/commitment-rules.md` | every commitment producer (capture floor reads the rules file), usage-report |
| `dont_forget_feedback` | Pulse `resolved` on a person-dormancy item ("expected" / "just busy") writes it with `{person_id, feedback}` (Phase 6 Loop 2/quick win B made the writer explicit — it drives the existing 14-day suppression the dormancy pass already reads at Phase 3 step 6, and Quick Win B ALSO widens `cadence_override_days`) | Pulse dormancy pass (14-day suppression skip), insight-generator Pass 14 (dismissal mining, BOTH families), usage-report |
| `session_sweep_run` | session-sweep nightly skill (Phase 5, R1) — one audit event per run: sessions scanned, events recovered | cleanup (R10 scheduled-output self-audit), value-receipt, weekly-recap |
| `session_backfill_run` | session-sweep historical backfill (Phase 5, R2) — one audit event per confirmed backfill batch | cleanup, value-receipt, usage-report |
| `m1_voice_proof_shown` | command-room-onboarding Phase 5b voice proof (shipped v4.4.0; registered here retroactively — it was written without enum registration, the exact drift this registry stops) | usage-report, coach (onboarding-beat telemetry) |
| `commitment_reclassified` | `commitment_state.promote_task_to_commitment` (Phase 2 Stage D — triage `make task`/`promote` verbs) + `shared/scripts/migrate_commitment_kinds.py` (S6 one-time partition, dry-run default) | the projector (`load_open_commitments` kind-override fold), `commitment_counts` by_kind, commitment-triage, `stale_tasks` |
| `commitment_reopened` | `commitment_state.reopen_commitment` (Phase 2 Stage D — S4 triage undo; also the reconcile-sent `undo` affordance may migrate here) | the projector (`load_open_commitments` order-aware closure state), `close_commitment` idempotency (a reopened item may be re-closed), commitment-triage |

The commitments/decisions/interactions the session sweep RECOVERS are written
as the existing `commitment` / `decision` / `interaction` types through
`append_event()` (dedup via `source_ref = "session:{session_id}"` in
`.source_refs.idx`) — no parallel "swept" variants of existing families.

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
- `commitment_update` is drift; the gate rewrites it to `commitment_updated`.
- `commitment_updated` — writers: the Commitments orchestrator `push to [date]`
  verb (`data: {commitment_id, new_due, reason}`) and the CRU schedule-shift
  path (`cru_match.build_commitment_updated_event`,
  `data: {commitment_id, change_summary, evidence}`). **Named consumer (Phase 2
  Stage A):** the commitment-state projector —
  `cru_match.load_open_commitments` folds the latest `data.new_due` (variants
  `due` / `due_date` accepted) into the returned commitment's effective
  `data.due`, so a deferred item stops rendering overdue. Before Stage A the
  type was write-only. Updates that carry no due field are informational and
  do not affect the fold.

## Read-side timestamp contract

Writers emit `ts` only (auto-stamped inside the writer lock). History is
additive-only forever — the 156 `timestamp` and 17 `date` spellings already
in live substrates are never rewritten. Every reader that orders or filters
events by time goes through `shared/scripts/event_time.py`
(`event_time` / `event_dt`), which resolves `ts` → `timestamp` → `date` in
priority order.
