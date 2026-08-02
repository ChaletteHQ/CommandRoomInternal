---
name: apply-choices
description: "Handles the Apply button on Command Room widgets — you never need to type this. Receives the consolidated 'apply choices: [...]' message a widget sends when the user clicks Apply (the daily chat surfaces, review widgets, onboarding setup), carries out each selected action through its owning skill (closures via the single closure path, drafts via email-writer, feedback into the capture stores), and posts ONE consolidated plain-English acknowledgment. Fires ONLY on the exact 'apply choices: ' prefix followed by a JSON array — never on natural language. Every action carries its source id, so no session state is needed. Action registry and per-surface handlers: Routing section in the body."
---

# apply-choices

Single dispatcher for the all-batch widget's "Apply all" submission. The widget renders per-item buttons that toggle local selection state — clicking buttons does NOT fire `sendPrompt`. Only when the user clicks "Apply all" does the widget serialize all selections and fire ONE consolidated message.

## ⛔ STOP CONTRACT (v2.14.14+) — applies to apply-time response too

**The post-Apply response surface follows the same rule as the originating orchestrator: widget + (optional) Briefs/Sources, then STOP.**

When apply-time produces drafts or regenerated docs, those surface in a NEW widget per Step 4A — NOT as standalone HTML files saved to disk, NOT as narrated summaries.

**Forbidden — zero tolerance:**

1. **No writing the post-Apply widget HTML to disk.** Not to `_hq/scheduled_outputs/`, not anywhere. The post-Apply widget surface is `show_widget` ONLY.
2. **No narrating "X drafts ready below"** before the widget. The widget self-describes via its header.
3. **No post-widget summary** like `Saved the standalone HTML at _hq/...` or `Regenerated all N drafts.` — same Rule 4 enforcement as the orchestrators.
4. **Re-renders on user re-prompt** ("regenerate with real data" against an apply-time response) use the same code path as the original Apply — re-execute Step 4A through the SAME pipeline (renderer → `show_widget`).
5. **Do NOT improvise a "save the apply output so the user can re-open" mode.** Saved HTML's buttons aren't wired to Cowork's `sendPrompt` — the saved file is dead on click. (v2.14.13 Edit-then-send dead-click — see references/HISTORY.md.)

**Self-check:** before posting anything outside a `show_widget` call, ask: "is this the SHORT plain-English ack per Step 4B, or is it freelancing?" If freelancing → STOP.

---

## Trigger pattern

EXACTLY one shape:

```
apply choices: <JSON array of {"n": <id>, "action": <string>, "sub": <optional string>, "input": <optional string|object>}>
```

Anything else → do not fire. This skill is not for general use.

The `input` field carries widget input-field values for actions that need user input. Two shapes:

- **String** (most actions) — free-text inputs (`resolved [reason]`, `context [text]`, `add more context [text]` (back-compat alias), `push meeting [date]` natural-language, `confirm [type]` overrides, `decide [text]`, etc.)
- **Object** (multi-field email edit) — `edit then send` and `draft` (the v2.14.4+ consolidated verb that replaced `to drafts` + `edit then draft`) carry an object with `to` / `cc` / `subject` / `body` keys.

The `sub` field is currently emitted ONLY by the `command-room-onboarding` Step 1 v2 widget (the role question's sub-chip drill-down). Other orchestrators do not produce it. The dispatcher does not interpret `sub` — it preserves the field intact and passes the full tuple to the source skill's reply handler. Do NOT strip or reject tuples with `sub` present. The allowed-keys set on each tuple is `{n, action, sub, input, src, context}` (`src` = emitting-surface id for stateless dispatch, W4; `context` = per-item note field); unknown additional keys are tolerated but ignored.

## Behavior

### Step 0 — Suppress the payload from chat history (v2.12.4+ — REQUIRED)

The user-facing text `apply choices: [{"n":...}]` is wire format. It must never appear as visible chat text.

When this skill fires, the user-typed message ALREADY shows in chat history above your response (Cowork echoes user submissions). The skill cannot remove it from chat. What it CAN do is render its own response as a Cowork-styled card so the substrate-y prefix gets visually overshadowed.

**Do NOT echo or paraphrase the payload.** Don't write "Received: [{...}]" or "Processing 5 actions" with the parsed array shown. The payload is internal mechanics — the user already knows what they clicked Apply on.

### Step 1 — Parse the payload

Strip the `apply choices: ` prefix. Parse the rest as a JSON array. Each entry must have:
- `n` — string or number, the item identifier (matches `data-n` from the widget; can be `1`, `7a`, `e1`, etc.)
- `action` — string, the action label (matches `data-action` from the widget; e.g. `send`, `mark received`, `push meeting [date]`)
- `sub` — optional string, present only for `command-room-onboarding` Step 1 Q1 sub-chip drill-down (e.g. `holdco`, `gp-fund`, `senior-staff`). Other orchestrators never emit it. The dispatcher passes this through verbatim — never strips or rewrites it.
- `input` — optional string OR object, the widget input-field value if the action needed one

If JSON parsing fails, surface plain English:

> *"Couldn't read your choices — refresh the chat and try again, or just tell me what you wanted ('send #2, push #4 to Friday')."*

Do NOT attempt to recover from a malformed payload — the widget produces well-formed JSON or it doesn't.

### Step 2 — Identify the source orchestrator

The widget was emitted by one of these orchestrators:
- `cr-dont-forget`
- `cr-commitments` (CTS1: `src: "commitments"` tuples now arrive from TWO chats — the Waiting On widget (`orchestrator-commitments.md`, taskId `waiting-on`: chase/confirm/nudged-no-reply rows) and the My Plate widget (`orchestrator-my-plate.md`, taskId `my-plate`: owner-me Promised/Personal rows). Both stamp the same `src` deliberately — dispatch keys on the row's embedded `data.id` and the verb, never on which chat the click came from, so the handler set is shared. **One row-class nuance (CTS1 §8.2 drip):** `reassign to [name]` on a My Plate row TAGGED counterparty-unresolved attaches the resolved person as the COUNTERPARTY — `commitment_state.reassign_commitment(workspace_root, <id>, new_counterparty_id=<resolved person_id>, new_counterparty_name=<display name>, reassigned_by=<user person_id>, reason="counterparty resolved from My Plate", source_skill="commitments", confirmed=True)` — owner stays the user; the item stays Promised, now linked. On every other row `reassign to [name]` remains the standard S4 OWNER reassignment. The row embeds the `counterparty_unresolved` context flag so this dispatch is stateless.)
- `cr-inbox`
- `cr-past-meetings`
- `cr-upcoming-meetings`
- `meeting-notes` (on-demand processing, Step 9 OPEN ITEMS section)
- `show-my-list` (v2.14.19+ — review surface for `commitment_to_discuss` items; the capture verb `add to my list` is RETIRED (MLK1, 2026-07-21), so this is now a DRAIN-ONLY fossil view: new renders offer `resolved` / `drop` per row and nothing feeds the backlog. **Handlers (Stage B 2026-07, supersedes the v3.11.4 direct-write shape):** `resolved` → close through **`commitment_state.close_commitment(workspace_root, <original commitment_to_discuss event's seq>, resolved_by=<user person_id>, evidence="resolved from my-list review", source_skill="apply-choices", user_confirmed=True)`** — the normalizer maps the bare seq to the canonical id and the write is the canonical `commitment_resolved` shape (NOT `thread_resolved` with `data.target_id`, the pre-v3.11.4 shape — see references/HISTORY.md). `drop` (MLK1 — the second drain verb) → same `close_commitment` call with `resolution="dropped"`, evidence "dropped from retired-list review". `skip` → write `chat_dismissal` event with `data.target_id` set to the original event's seq (unchanged from pre-v3.11.4 — chat_dismissal is its own canonical pattern; no NEW render offers it here post-MLK1, but persisted old list widgets still dispatch it). For backwards-compat, show-my-list's render filter accepts BOTH the new `commitment_resolved.data.commitment_id` AND legacy `thread_resolved.data.target_id` as valid closers, so in-flight items from pre-v3.11.4 still close correctly. **Mute-ledger rows (v4.6.0 S4 — the `show muted` / `show snoozed` widget, same `src`):** `unmute` → `mute_ledger.clear_dismissal(workspace_root, <row's dismissal seq verbatim>, cleared_by=<user person_id>, source_skill="show-my-list")` — additive `chat_dismissal_cleared`; honor `already_inactive` as a NO-OP with an honest ack ("that one had already expired"); the ack for a real clear says when the item will re-surface ("Unmuted — it'll be back on its next chat."). `skip` on a ledger row → standard `chat_dismissal` (24h) targeting the LEDGER ROW rendering, never the underlying mute.)
- `commitment-triage` (Phase 2 Stage D, S4 — the `triage my commitments` surface + the Friday opt-in scheduled chat. **Dispatch (all through `commitment_state` — NO in-place mutation ever, F4):** `resolved` → `close_commitment(workspace_root, <data.id verbatim>, resolved_by=<user person_id>, evidence="triaged: done", source_skill="commitment-triage", user_confirmed=True)`; `drop` → same with `resolution="dropped"`, evidence "triaged: dropped"; `not mine` → same with `resolution="dropped"`, evidence "not the user's item (cross-attendee capture)" — but if the user NAMED the real owner (input or chat phrase), route via `reassign to [name]` below instead of dropping; `push to [date]` (displays **Later…**, t3 FB-3) → AUTO-ROUTE by ownership: parse the when via `commitment_state.parse_later_when(<input>, <now_iso>)` FIRST (bare number = days from today, ISO date passes through; `None` → fall back to the standard natural-language date parse), then `commitment_state.later_route(<the item's commitment event>, <user person_id>)`: **"defer"** (the user's own item) → `commitment_updated` with `data.new_due` (canonical shape per orchestrator-commitments `N push to [date]`, unchanged); **"snooze"** (owed-to-you / unowned) → `chat_dismissal` with `data.target_id` = the commitment id verbatim and `data.snooze_until` = the parsed date (the mute-ledger field — the item stays OPEN, it just stops rendering until then; never move a due date the counterparty owns from a view-management click). Ack says which happened in plain English ("moved to Friday" vs "hidden until Friday — still theirs to deliver"); `make task` → `promote_task_to_commitment(workspace_root, <id>, new_kind="task", source_skill="commitment-triage", reason="user triaged to task")`; `promote` → `promote_task_to_commitment(..., new_kind="promise", reason="counterparty appeared — user promoted")`; `never track this` → append the item's suppression pattern (title-shape, one line) to `_hq/config/commitment-rules.md` via atomic write + `close_commitment(..., resolution="dropped", evidence="never-track rule added")`; `skip` → `chat_dismissal` (24h, unchanged). **Lifecycle verbs (v4.6.0 S4):** `fix wording [text]` → `commitment_state.edit_commitment_wording(workspace_root, <data.id verbatim>, new_summary=<input> (or new_title when the item renders title-only), edited_by=<user person_id>, source_skill="commitment-triage")` — the projection re-renders the new text; history keeps the original; ack shows the corrected line; `reassign to [name]` → resolve the typed name to a person via the standard entity-resolve path (aliases first; ambiguous → disambiguation widget, never guess), then `commitment_state.reassign_commitment(workspace_root, <id>, new_owner_id=<resolved person_id>, new_owner_name=<display name>, reassigned_by=<user person_id>, reason="user reassigned in triage", source_skill="commitment-triage", confirmed=True)` — confirmed=True because the user explicitly named the person; the item leaves their you-owe and lands on the new owner (ack: *"Routed to Quinn — it's on their side of the ledger now."*); `split into [items]` → parse the input into 2+ child titles (split on newlines / semicolons / " / "), then `commitment_state.split_commitment(workspace_root, <id>, [{"title": ...}, ...], split_by=<user person_id>, source_skill="commitment-triage", user_confirmed=True)` — N new items each carrying the original's provenance, the original closes with a "split into …" note (ack names the N new items); a parent with open sub-items REFUSES to split (ValueError — surface its message verbatim: close/drop the sub-items first, or `add subitems` to decompose further). **Sub-items (SUB1):** `add subitems [items]` → parse the input with the SAME newline/semicolon/" / " rule (≥1 title is valid here — one named step is a legitimate decomposition, unlike split's ≥2), then `commitment_state.add_subitems(workspace_root, <parent data.id verbatim>, [{"title": ...}, ...], added_by=<user person_id>, source_skill="commitment-triage", user_confirmed=True)` — the parent STAYS OPEN as the commitment of record; N child commitments land under it (widget re-renders them nested with the parent's progress chip). Chat phrases: "break #N into: A / B / C", "add sub-items to #N: …", "steps for #N: …". Loud writer errors surface verbatim: >12 open children (a 13-step item is a project), or a parent that is itself a sub-item (one level deep, no grandchildren). Child rows dispatch like any commitment row on their OWN `data.id` (Done/Later…/Drop/Not mine/Turn into a task/Skip — children are real commitments; `never track this` stays parent-level). **Parent Done with open sub-items (cascade, SUB1 D3):** `resolved`/`drop` on a parent whose row shows open sub-items → `close_commitment` raises `OpenSubitemsError`; NEVER swallow it — ask the one-line confirm ("this also closes its N open sub-items — go ahead?"), and only on the user's yes re-dispatch with `close_subitems=True, user_confirmed=True`. The cascade closes children first, parent last, and returns `closed_subitems` (the child ids). When the LAST open child closes normally, the parent row shows "all sub-items done — close it?" — that is a PROPOSE, never an auto-close. **Undo (S4):** cache the batch's closed/reclassified ids — INCLUDING every id in a cascade's `closed_subitems` return (SUB1: an undone parent-cascade must reopen the whole family; reopen is per-item, each child has its own tombstone) — AND the seqs of every `chat_dismissal` the batch wrote in the fire-marker; a follow-up `undo` in the same chat reopens each closed item via `commitment_state.reopen_commitment(workspace_root, <id>, reopened_by=<user person_id>, reason="triage undo", source_skill="commitment-triage")`, reverses each reclassification with an opposite `promote_task_to_commitment` call, AND clears the batch's mutes via `mute_ledger.clear_dismissals(workspace_root, <cached dismissal seqs>, cleared_by=<user person_id>, source_skill="commitment-triage")` (the F-20 P3a asymmetry, fixed — undo used to reopen items while leaving their mutes in force). Additive only — tombstones, markers, and clears stay in history. Ack: *"Reversed — N items back open, M mutes lifted."*)
- `backlog-sweep` (SWEEPBACK 2026-07-30 — the on-demand commitment backlog sweep's digest, `commitment-backlog-sweep`. Every row embeds `data.id` verbatim plus `data.bucket` (`proposed` / `merge` / `age_out`), so dispatch is stateless and never guesses which pile a row came from; the `auto_closed` rows carry NO actions at all, because those closes already happened and the honest affordance for them is `undo` on the run's batch. **Dispatch — one call, never per-row writes:** collect the selected tuples into `decisions = [{'commitment_id': <row's data.id verbatim>, 'bucket': <row's data.bucket>, 'action': <the verb>, 'absorbed_ids': <row's data.absorbed_ids, merge rows only>, 'evidence': <row's context line>}]` and hand the whole list to `commitment_backlog_sweep.apply_decisions(workspace_root, decisions, user_person_id=<user person_id>, batch_id=<the batch id the digest showed>)`. That helper closes through `commitment_state.close_commitments` and merges through `commitment_state.supersede_commitment(user_confirmed=True)` — do NOT call either directly from here, and do NOT hand-build a closure. Passing the digest's own `batch_id` is what keeps the whole sweep run — its automatic closes and everything the user just confirmed — inside ONE `swb_` batch that a single `undo` reverses. Verbs: `mark done` (proposed → closes, resolution `done`), `drop` (age_out → closes, resolution `dropped`, stamped as an age-out so it stays distinguishable from a deliberate drop), `merge` (folds into the OLDEST item), and `still valid` / `keep both` / `skip` which write NOTHING — leaving something alone is not an event. Honor `already_resolved` as a NO-OP with an honest ack. Ack: `receipt['summary']` verbatim.)
- `command-room-onboarding` (v3.4.1+ — first-install Step 1 setup widget. Fire-marker event type: `onboarding_setup_widget_emitted` with `source_skill: command-room-onboarding` and `data.widget_kind` of `step_1_setup` (pre-v2, 4 tuples), `step_1_setup_v2` (3 tuples: role / email exclusions / timezone), `step_1_setup_v3` (4 tuples, M1: + AI name), `step_1_setup_v4` (5 tuples, SPEC FRP1: role / email exclusions / timezone / AI name / email draft posture), or `step_1_setup_v5` (current: 4 tuples — role / timezone / AI name / email draft posture; the email-exclusion question was dropped 2026-06-30). Source identification keys on `source_skill`, NOT `widget_kind` or tuple count, so all payload shapes dispatch. Item 1 may carry an optional `sub` field from the role question's sub-chip drill-down, and any item may carry an optional `input` from a refinement textbox. Dispatch routes to `skills/command-room-onboarding/SKILL.md` Section "Reply handling — Phase 0 setup" — that section maps each `(action, sub?)` pair to the appropriate write by topic: role / timezone / AI name to entities.json `workspace.*`, and email draft posture (`show_first` / `auto_queue`) to email-writer's `skill_config` via `save_skill_config(..., origin="m1_batch")` — NOT entities.json. A legacy v4 payload's email-exclusion tuple is ignored. Then resumes onboarding flow control at Phase 1. The actions are selection labels, not action verbs from `CANONICAL_ACTIONS` — apply-choices Step 3 parses this payload but does NOT validate the actions against `CANONICAL_ACTIONS` for this source.)
- `morning-brief` (P0.7 2026-07 — the Morning Brief chat's `mark done [n]` affordance, morning-briefing Step 3b / Bug #85. Morning Brief is a markdown digest, not a widget — the action normally arrives as a typed `mark done [n]` reply in that chat; treat it exactly like a widget tuple with `src: "morning-brief"`. Fire-marker event type: `pack_run` with `source_skill: "morning-brief"`; its `data.needs_attention_ids` records the commitment id (`data.id` verbatim) for each numbered Needs Attention item in render order. **Handlers:** `mark done [n]` → resolve `[n]` against `needs_attention_ids` from the most recent morning-brief `pack_run`, then close through `commitment_state.close_commitment(workspace_root, <that id verbatim>, resolved_by=<user person_id>, evidence="marked done from morning brief", source_skill="apply-choices", user_confirmed=True)` — the canonical closure path (Stage B); never a hand-built `commitment_resolved` append. If `[n]` exceeds the recorded list or the list is absent (pre-P0.7 fire), ack with the error line below instead of guessing. No other actions dispatch from this source.)
- `decision-revisit` (P1.1 2026-07 — the revisit widget's per-decision deliberation set. Each item embeds the original decision event's id/seq verbatim. **Handlers:** `revisit` → invoke `decision-memo-composer` pre-filled with the original decision's framing + the contradictory-signal pass (no event write here — the composer's flow owns downstream writes); `still valid` → append `decision_reaffirmed` referencing the original decision id (locked writer; omit seq — auto-stamped); `replace` → chain to `decision-log` to capture the new decision, then append `decision_superseded` linking the original; `snooze 30d` → append `decision_revisit_scheduled` with `snooze_until_ts = now + 30d`; `skip` → `chat_dismissal` (24h).)
- `decision-memo-composer` (P1.1 2026-07 — the Phase 6 memo widget, fully respecced onto canonical verbs. The memo .docx is already saved (and `decision_memo_drafted` already written) at render time — no save button exists. **Handlers:** `decide [text]` → chain to `decision-log` to write the canonical `decision` event with the memo .docx as rationale link (non-empty text folds into the rationale); `edit [change]` → re-run the composer's Phase 4 with the new weights from the input; `skip` → no write, the memo stays a draft on disk.)
- `stalled-projects` (P1.1 2026-07 — the stalled-list widget, respecced onto canonical verbs. **Handlers:** `draft re-engagement` → open `email-writer` / `follow-up-ritual` for that project (lazy draft — nothing sends); `mark paused` → update the project's status via `workspace-manager`'s writer (never a direct entities.json edit from here); **`archive` (SPEC ARCHFIX 2026-08 — the row has offered this button since P1.1 and nothing here handled it, so the click landed nowhere)** → `thread_archive.archive_thread(workspace_root, <the row's `thread_id` verbatim>, reason=<`thread_archive.stalled_review_reason(<the row's `days_since_activity`>)` — "archived from stalled-projects review — quiet N days" — unless the user typed their own reason, which wins>, source_skill="apply-choices")`, the SAME typed write path workspace-manager's `archive [project]` runs: it stamps `status`/`archived_at`/`archive_reason` through `thread_writer.update_thread`, appends ONE `status_change` event, and regenerates MASTER_TRACKER (never a direct entities.json edit from here, never a tracker-markdown edit — a markdown archive is erased by the next render). The user's click IS the confirm; nothing auto-archives, and no other verb on this row may reach `archived`. `handler_result` is the returned dict; a `status` of `already_archived` is an honest no-op (no second `status_change`) and the ack says so, and a `ThreadArchiveError` means the row's id no longer resolves — report it, never archive an adjacent project. Ack in plain language naming the project and the reason on file; `status check` → surface the project's recent-events digest inline; `snooze 14d` → `chat_dismissal` with 14d TTL; `skip` → `chat_dismissal` (24h).)
- `scaffold-automation` (P1.1 2026-07 — the deployed-yet? check widget. The setup recipe is an H2 doc link in the widget body, not a button. **Handlers:** `mark done` → append the skill's `automation_deployed` event per its Writer Contract; `snooze 7d` → `chat_dismissal` with 7d TTL (re-surfaces the check in a week); `skip` → `chat_dismissal` (24h).)
- `show-my-reminders` (v4.5.2 S2 — reminder rows on any widget surface: the brief's Pinned/Upcoming blocks and a future `show my reminders` widget both emit this source. Each row embeds the reminder's `data.id` verbatim. **Handlers (all through `shared/scripts/reminders.py` builders + `event_gate.append_event` — never hand-built payloads):** `reminder done` → `build_reminder_cleared_event(<id>)` — clears the pin; if the user's phrasing says a REFERENCED commitment is also done, that closes through `commitment_state.close_commitment` as its own action (two events, two acks); `reminder push [date]` → `build_reminder_updated_event(<id>, action="push", remind_from=<parsed date>)` — the date is REQUIRED and the widget already enforced it (F-17), but if a legacy payload arrives dateless, ack the miss plainly and dispatch nothing for that row; `reminder keep` → `build_reminder_updated_event(<id>, action="keep")` — resets the escalation clock. Ack in reminder language ("Cleared 1, deferred 1 to Friday, kept 1 pinned."), never event-type names.)
- `cr-pipeline` (SPEC PIPE1 — the pipeline-tracker ranked-report widget. Each row embeds the deal's thread id verbatim; untracked rows (pre-existing deal threads with no stage tracking) carry only `track deal` + `skip`. **Handlers (every deal mutation through `shared/scripts/deal_state.py` — the single writer/closure path; NO hand-rolled entities.json or event writes, ever):** `move to [stage]` → `deal_state.set_stage(workspace_root, <thread id verbatim>, <picked stage>, source_skill="pipeline-tracker")` — the picker SHOWS the plain labels from `deal_state.STAGE_DISPLAY` (Lead / Qualified / Proposal sent / Negotiating — won/lost are not stages) and DISPATCHES the wire ids; snake_case enum ids never render (UXC1); `mark won` → `deal_state.close_deal(workspace_root, <thread id>, "won", source_skill="pipeline-tracker")` — NEVER pass convert_prospect from a widget click (D6: only the explicit "[Name] signed" utterance family converts); when the return carries `conversion_suggestion`, the ack renders it verbatim ("Acme Co is still marked a prospect — say `Acme Co is now a client` and I'll convert them"); honor `already_closed` as a NO-OP with an honest ack; `mark lost [reason]` → `deal_state.close_deal(workspace_root, <thread id>, "lost", loss_reason=<picked enum>, loss_note=<optional typed text>, source_skill="pipeline-tracker")` — reason REQUIRED (F-17: an empty reason holds Apply with the reason visible; the picker SHOWS `deal_state.LOSS_REASON_DISPLAY` labels — No decision / Price / Competitor / Doing it themselves / Timing / Bad fit / Other — and DISPATCHES the wire enum; snake_case never renders, UXC1); `set next step [text]` → a standard `commitment` capture with `primary_thread_id` = the deal thread and an EXPLICIT `data.kind` (`promise` when a counterparty is owed, `task` when it's the user's own move — never rely on the gate's default stamp); parse a date from the text (undated is allowed but the ack nudges for one) — closes only via `commitment_state.close_commitment` like any commitment; `track deal` → `deal_state.adopt_deal(workspace_root, <thread id>, source_skill="pipeline-tracker")` — attaches stage tracking to an untracked deal thread (defaults stage=lead; the ack offers 'move [deal] to [stage]' to correct it); `draft re-engagement` → open `email-writer` for the deal's org contact with the deal thread's context (lazy draft — nothing sends); `snooze 14d` / `skip` → standard `chat_dismissal` (14d / 1d) targeting the deal row. Ack in deal language ("Moved 1, closed 1 won, snoozed 2."), never event-type names.)
- `balance` (SPEC BAL1 — the Sunday personal white-space reconnect card, `surface: "m_facing"`; each row embeds the tie's `person_id`, the fire's `nudge_seq` (the card's identity — it is what makes the confirm idempotent per card), and the fire's `open_slots`, all verbatim. **PERSONAL-LANE: every ack stays owner-facing; nothing from this dispatch is ever copied into an org/board/client surface.** **Handlers:** `book` → the BAL1 D4 propose-and-confirm pair, BOTH legs user-click-gated by THIS dispatch and nothing earlier: (1) the tentative personal-calendar hold routes through `calendar-writer`'s Phase 5/6 consent path targeting the personal calendar id from `workspace.personal_calendars` (never a direct calendar write); (2) any venue outreach draft (already composed via the email-writer chain at render time) queues per the workspace draft posture — never auto-sent, never auto-booked, nothing spends; then close the loop through **the single gated writer** — `balance.record_actioned(workspace_root, tie_person_id=<the row's person_id verbatim>, source_nudge_seq=<the row's nudge_seq verbatim — REQUIRED, it is the idempotency key>, draft_event_seq=<the queued draft's seq, or None with no email leg>, venue=<the confirmed venue or None>, hold_start=<ISO start of the held evening, or None>, source_skill="balance")`, NEVER a hand-rolled append. Its whole job is to append the follow-on linkage as a `balance_nudge_actioned` event (`data.personal: true` always — personal-classified via `personal_leak._PERSONAL_EVENT_TYPES`; an executing model must NEVER substitute a generic or org-lane type here, or the linkage's venue/date content lands in org-scoped reads) carrying `proposed_action.draft_event_seq` (append-only — never edit the original nudge event). It raises on a tie that is not an active personal-lane person and on a missing `source_nudge_seq`. Idempotency is keyed on `(person_id, nudge_seq)` — the CARD, not a clock: re-dispatching the same card returns `already_actioned` permanently (an honest NO-OP, acked as "already held", never a duplicate linkage), while a genuinely new card writes a new linkage immediately, same week or not. If that return carries a `diverged` map, the ack names what was kept — "already held Thursday at [recorded venue]; I did not change it to [passed venue]" — never a bare "already held" over a discarded correction. Optional input = a venue name/correction folded into the draft before queueing. `propose other night` → typed date: validate via `availability.has_conflict` against the fire's own busy set; conflicted → honest ack naming the conflict + the closest open slot, NO writes; clean → re-render the card pinned to the new evening (still propose-and-confirm — no hold until a `book` click). Empty input → re-render the remaining `open_slots` from the fire's computation. `snooze 7d` → `chat_dismissal` with `data.snooze_until` = now + 7 days (snooze_until is the field the mute ledger honors — a bare TTL number silently falls back to the 24h legacy default) and `data.target_id` = the tie's `person_id` verbatim (that id is how the next fire's dedupe recognizes the snoozed tie; matches the surface's own per-tie dedupe window); `skip` → `chat_dismissal` (24h, same target_id shape). Ack in plain personal language ("Held Thursday evening and queued the note — nothing sends until you do."), never event-type names, never scores.)
- `cr-objectives` (SPEC OBJ1, DRAFT — the objectives readout widget + cold-start proposal cards. Each row embeds the objective's thread id verbatim. **Handlers (every objective mutation through `shared/scripts/objective_state.py` — the single writer/closure path; NO hand-rolled entities.json or event writes, ever):** `report [status]` → `objective_state.record_report(workspace_root, <thread id verbatim>, status=<picked enum>, reported_by=<user person_id>, source_skill="objectives")` — the picker offers exactly on_track / at_risk / off_track / blocked (the owner's word; never inferred); `mark complete` → `objective_state.complete_objective(workspace_root, <thread id>, source_skill="objectives")` — honor `already_closed` as a NO-OP with an honest ack; `archive [reason]` → `objective_state.archive_objective(workspace_root, <thread id>, outcome_note=<optional typed text>, source_skill="objectives")` — the graceful-death "archive" click lands here too; `rebind` → open the objectives skill's rebind flow (path toggle + proposed target + confirm — the write lands via `objective_state.rebind_objective`, no write from this dispatcher); `confirm objective` (cold-start proposal cards only) → `objective_state.create_objective(workspace_root, statement=<the card's drafted statement — typed input replaces it>, name=<the card's short name (2-4 words) — the entity-resolve match surface>, binding=<the card's pre-filled binding>, owner_person_id=<the card's proposed owner>, anchor_thread_id=<the card's anchor, when present>, source_skill="objectives")` — propose-and-confirm always; a skipped proposal stays away 60 days (receipt trail); `snooze 14d` / `skip` → standard `chat_dismissal` (14d / 1d) targeting the objective row. Ack in objective language ("Heard you — 2 on track, 1 at risk; archived the handbook push."), never event-type names.)
- `boardroom` (P1.1 2026-07 — the Round 1 hardest-questions widget. Each question row carries a free-text answer input; `skip all` (the canonical bulk verb) advances straight to verdicts. **Handlers:** answered rows → pass the answers back into the boardroom flow (in-chat continuation — no substrate write from this dispatcher); `skip all` → resume the boardroom flow at the verdicts round. No other actions dispatch from this source.)
- `cr-brain` (SPEC LB1 2026-07 — the Living Brain "Needs your eyes" card (morning brief / coach) and the Staff Meeting full-queue widget. Rows come from `brain_proposals.load_open_proposals` / `select_confirm_card` — ONE queue mixing brain-family (`bp_*`) proposals with adapter-read legacy items; each row embeds its proposal id + target ids VERBATIM and carries `context.kind` naming its family. Dispatch is per-kind — brain rows resolve through `brain_proposals.resolve_proposal`; legacy rows dispatch through their OWN shipped flows (the adapters are permanent fossil readers — LB2 migrated the org/project/dormancy/schedule_add WRITERS onto the bp rail, so NEW rows of those kinds arrive as `bp_` ids and dispatch through the brain-family handlers below; only pre-migration rows still arrive as legacy ids. person and commitment_review remain legacy-written — LB3). **Handlers — brain family (id starts `bp_`):** `confirm proposal` → apply the proposed change through its class's single writer FIRST, then `brain_proposals.resolve_proposal(workspace_root, <bp_ id verbatim>, "applied" (or "edited" when input was typed), resolved_by=<user person_id>, source_skill="apply-choices")`. Per kind: `deal_update` → `deal_state.set_stage` / `update_deal` / `close_deal` per the proposal's `proposal_kind` (won/lost only via `close_deal`; `mark lost` requires the reason enum exactly as on `cr-pipeline`); `deal_creation` → **coverage check FIRST (FS-18b):** run `deal_state.org_deal_coverage(<entities threads>, <org_id verbatim>)` — the SAME predicate the detector consults; if it returns a thread, the org's deal activity is already tracked (an open deal or an active engagement thread), so do NOT create: resolve the proposal **`superseded`** (review F-4 — the user clicked Confirm, they did NOT decline; `superseded` is the truthful label for "the premise no longer holds", and it carries the SAME 60d cooldown as declined so the covered item never re-proposes) with a note naming the covering work (e.g. "already covered by [thread display name]"), and ack honestly (*"That one's already tracked — [name] covers it, so I closed the suggestion."*). This also self-heals any pre-T2.2 zombie proposal still open in a live queue. Uncovered → `deal_state.create_deal(workspace_root, name=<proposed>, org_id=<org_id verbatim>, stage=<proposed stage or "lead">, source_skill="apply-choices")` — value only when the proposal carried a user-visible amount, never estimated; `person_update` (HIST1 Part 2 — an entity-signal row about a person ALREADY on file; the row embeds `data.person_id` verbatim + `data.proposal_kind`): `confirm proposal` with `proposal_kind: role_change` → `people_writer.update_person(workspace_root, <data.person_id verbatim>, role=<data.proposed_role — or the user's typed input when they corrected it>, source_skill="apply-choices")`; with `proposal_kind: org_change` → `update_person(..., primary_org_id=<data.to_org_id verbatim>, ...)` — in BOTH cases the writer's Part 1 hook auto-emits the `person_role_changed` / `person_org_changed` lineage event (the old value is preserved as history; do NOT also record a fact for the same change), then `resolve_proposal(..., "applied")`; `kind: entity_fact` (HIST1 Part 2 — a prose-inferred or identity-adjacent fact observation; the row embeds the target id + `data.fact` + `data.category` + `data.source_ref`): `confirm proposal` → `people_writer.record_person_fact` / `org_writer.record_org_fact` (`workspace_root, <target id verbatim>, <data.fact — or the user's edited wording from input>, <data.source_ref, falling back to "proposal:<bp_ id>">, category=<data.category>, source_skill="apply-choices"`) — an additive event, never a record field — then `resolve_proposal(..., "applied")`; `kind: org_money` (HIST1 Part 2 — an observed account/contract value near a client org; the row embeds `data.org_id` verbatim + `data.proposed_money`): `confirm proposal` → `org_writer.set_org_money(workspace_root, <data.org_id verbatim>, <data.proposed_money — the user's typed amount wins over the proposed one>, source_skill="apply-choices", confirmed=True)` (`money.source` = the proposal's evidence ref — money is always sourced, never estimated; the user's click IS the confirm that makes `confirmed=True` truthful), then `resolve_proposal(..., "applied")` — dismiss/snooze for all three kinds are the standard bp handlers below. An empty input applies the proposal as-is and the ack says so (F-17: a missing optional input NEVER holds the batch); **LB2 migrated-kind confirms (same per-kind writers the legacy dispatch below names — migration changed where rows come from, not what clicking them does):** `org` → `org_writer.create_org` (dedup-first through `find_existing_org`), then `resolve_proposal(..., "applied")`; `project` → workspace-manager's new-project writer, then `resolve_proposal(..., "applied")`; `dormancy` → the dont-forget orchestrator's status handlers (`status_change` to dormant via the canonical writer), then `resolve_proposal(..., "applied")`; `schedule_add` → the row is a POINTER — registration happens ONLY through the change-schedule / registration add path, never from this dispatcher; on the user's add, `resolve_proposal(..., "applied")` closes the row; `config_drift` (LB2 §3b — the row re-offers ONE drifted setting, staff-meeting only) → append ONE `note` event via `event_gate.append_event` with `data: {skill: <row's data.skill verbatim>, knob: <data.knob verbatim>, origin: "drift_reoffer", summary: <the row's render_line>}` — the next coach session consumes it and re-offers THAT KNOB through the tune flow — then `resolve_proposal(..., "applied")`; **apply-choices NEVER writes `skill_config` from this row** (the tune flow is the only config writer; cleanup stays read-only on prefs); `kind: objective_link` (OBJ2 §2 — the staff-meeting "link it?" row from the objective-link detector; the proposal payload embeds `objective_id` + `target_id` + `source_event_seq` verbatim): `confirm proposal` → the CONFIRMING reclassification+feedback pair (OBJ2-R, the mirror of dismiss's — the two writes always land together), THEN the tombstone: (1) read the source event by the payload's `source_event_seq` verbatim and append ONE `reclassification` event via `event_gate.append_event` — `source_skill: "apply-choices"`, `supersedes_seq` = that seq, envelope = the source event's own envelope UNCHANGED (primary and related ids verbatim — a confirm asserts the classification, it never moves it), `classification_confidence: 1.0` (the user's click IS the confidence), `data: {old_primary_thread_id, new_primary_thread_id (equal), old_related_thread_ids, new_related_thread_ids (equal), reason: "user confirmed objective-link proposal <bp_ id> — link to <objective_id>"}`; (2) append the MATCHED `classifier_feedback.jsonl` row (`user_action: "confirmed"`, `new_primary` = old_primary per the schema, `confidence_before` = the source event's `classification_confidence` — `0.0` when the event carries none (a pending_review-only provisional; the schema requires a number), `notes: "objective link confirmed: <objective_id>"`); same half-pair rollback rule as dismiss; (3) `brain_proposals.resolve_proposal(workspace_root, <bp_ id verbatim>, "applied", resolved_by=<user person_id>, source_skill="apply-choices")` — the applied tombstone (the detector's confirmed-fingerprint dedup consumes it, so the pairing never re-lists; still no `objective_state` call — `pending_review` is stamped, never unset, by design, and rebind is the wrong contract). The supersession-aware movement read (`thread_activity.apply_reclassifications`, consumed by `objective_math` + the detector, and since RECL1 by every recency surface via the `honor_reclassifications` seam) is what makes this click COUNT: the confidence-1.0 envelope clears the activity floor, and the objective's own thread joins the movement read. A pre-OBJ2-step-2 row whose payload lacks `source_event_seq` skips the pair-writes and just resolves applied. Ack in plain link language (*"Linked — noted as a move on [objective name]."*); `dismiss proposal` on an objective_link row → the standard reclassification+feedback pair (the insight-generator Pass 8 idiom — the two writes always land together), THEN the decline: (1) read the source event by the payload's `source_event_seq` verbatim (NEVER re-derive it at dispatch time) and append ONE `reclassification` event via `event_gate.append_event` — `source_skill: "apply-choices"`, `supersedes_seq` = that seq, envelope = the source event's own envelope with the row's objective thread removed (the objective was the primary → new primary is `null`; otherwise it leaves `related_thread_ids` and the primary stands), `classification_confidence: 1.0`, `data: {old_primary_thread_id, new_primary_thread_id, old_related_thread_ids, new_related_thread_ids, reason: "user declined objective-link proposal <bp_ id> — unlink from <objective_id>"}` — never edit the original event; (2) append the MATCHED `classifier_feedback.jsonl` row via `atomic_append_jsonl`, schema-valid (`{ts, event_seq: <source_event_seq>, user_action: "changed", old_primary: <the source event's primary>, new_primary: <the new primary from (1)>, signals_used: [], confidence_before: <the source event's classification_confidence — 0.0 when the event carries none (a pending_review-only provisional; the schema requires a number)>, notes: "objective link declined: <objective_id>"}`); if the reclassification append fails, write NO feedback row and surface in `_hq/CONFLICTS.md` (the Pass 8-K rollback rule — never a half pair); (3) `brain_proposals.resolve_proposal(..., "declined", ...)` — the tombstone + the shared 60d cooldown keep the pairing away. A re-dispatched row that is already adjudicated (absent from `load_open_proposals`) skips the pair-writes entirely and goes straight to `resolve_proposal` — its `already_resolved` return is the honest no-op; the pair never duplicates. A pre-OBJ2-step-2 row whose payload lacks `source_event_seq` also skips the pair-writes (nothing to supersede against) and just declines. `handler_result` for EVERY objective_link action is resolve_proposal's return — the pair-writes are never passed as handler_result (FS-18a). Dismiss ack: *"Dropped it — I won't suggest that pairing again."* (the unlink is honored at read time — `objective_math`, the detector, and since RECL1 every recency surface (stalled-projects, Pulse, the master tracker, list-active, the workspace map, DCC, deal rot, org history, `go [name]` ranking) fold reclassifications via `thread_activity.apply_reclassifications`, so a dismissed pairing stops counting everywhere and can never re-propose even after the 60d cooldown lapses; OBJ2-R closed the objective-reader gap, RECL1 the rest); `snooze proposal 7d` → the standard bp snooze below; `dismiss proposal` → `brain_proposals.resolve_proposal(..., "declined", ...)` — the tombstone + the shared 60d cooldown row; deal-kind declines also emit `deal_update_dismissed` (handled inside resolve_proposal — do not append it here); `snooze proposal 7d` → `chat_dismissal` with `data.snooze_until` = now + 7 days (snooze_until is the field the mute ledger honors — a bare TTL number silently falls back to the 24h legacy default) and `data.target_id` = the proposal id verbatim. **Handlers — legacy families (PRE-migration fossil rows plus the still-legacy-written person / commitment_review families — route to the family's existing dispatch, exactly as its own surface would):** `kind: commitment_review` → `confirm` NEVER closes row by row from here (WATCHGATE §2.2 — one gesture once turned six of these into "done" in two seconds, every one of them resting on nothing but a title match). Collect EVERY confirmed `commitment_review` row in the batch and dispatch them in ONE call: **`watch_gate.confirm_review_rows(workspace_root, rows, resolved_by=<owner_id>, individually_named=<only the wire ids the user named on their own>, source_skill="apply-choices")`** — THE shared bulk-accept fence, the same one the needs-your-call queue calls. Each row: `{"id": <wire id verbatim>, "commitment_id": <verbatim>, "evidence": <the row's `evidence`>, "match_score": <the row's `match_score`>, "has_completion_signal": <the row's `has_completion_signal`>, "evidence_ts": <the row's `evidence_ts`>, "promise_ts": <the row's `promise_ts`>}` — **every one of those keys is already ON the queue row** that `brain_proposals.load_open_proposals` handed you; copy them across verbatim and derive none of them. A key the row does not carry is passed as `None`, which means "not assessed" and weakens nothing. (The row also carries `strength` and `weak_reason` — that is the badge the user already read, not an input; do not recompute it.) Inside that call a STRONG row closes through `commitment_state.close_commitment(..., user_confirmed=True)` exactly as before, and a WEAK row — no evidence, title-match-only evidence, no completion signal, or an ordering that says the meeting has not happened yet — is HELD and PARKED on watch instead. `individually_named` may carry a wire id ONLY when the user typed that row's own number or name as a standalone answer; "Apply all", a range and a group phrase name nothing individually, so they pass an EMPTY set. **Never re-issue the call with the held ids in `individually_named` on your own** — that override belongs to the user's next sentence. `handler_result` for each row is its own entry in the returned `results` (`status` is `closed`, `watching`, or a refusal). Ack from the returned `ack` line: what was confirmed, what is on watch, and that naming one by its own number keeps it now — never scold, never explain the fence; `not relevant` → `cru_match.build_commitment_review_dismissed_event` + `event_gate.append_event` (60d, the commitment STAYS OPEN); `hold` → `mute_ledger.hold_item(workspace_root, <row id verbatim>, source_skill="apply-choices", surface=<the firing surface>)` (FB-19 — 14d, the commitment STAYS OPEN; see the hold handler below); `kind: person` (T2.2/FS-17 — the enriched identity rows carry `add person` / `proposal not relevant` / `snooze proposal 7d`): `add person` → **`people_writer.auto_add_person(workspace_root, canonical_name=<the proposal's name>, email=<ONLY an address observed in the proposal's own evidence/source_ref text>, email_provenance=<the observed source ref — REQUIRED whenever email is passed; a pattern-guessed or constructed address is NEVER written (F-08 extends to capture)>, role=<inferred_role or None>, source_skill="apply-choices")`** — the same-name dedup gate runs INSIDE the call; a `needs_confirm` return renders the Step 3a disambiguation widget instead of writing, and the ack says so honestly. On `added`, append the `person_added` tombstone via `confirm_flow.build_person_proposal_resolved_event` (the entity write FIRST, then the tombstone — never skipped). **Cluster fan-out (PID1 D3):** a person row may embed `data.cluster_seqs` (plus `data.cluster_fingerprints` for seq-less legacy rows) — the identity-clustered row covering EVERY underlying proposal for that person. `add person` runs **ONE** `auto_add_person` call, then a tombstone **per** entry: one `build_person_proposal_resolved_event(<seq>, resolution="person_added", person_id=<new id>)` per int in `cluster_seqs` and one `build_person_proposal_resolved_event(None, ..., proposal_fingerprint=<fp>)` per entry in `cluster_fingerprints`; `proposal not relevant` → a `not_relevant` tombstone per entry the same way; `same as [existing]` → ONE `add_person_alias` then a `same_as` tombstone per entry. When the row carries NO `cluster_seqs`, fall back to the single embedded seq — back-compat with in-flight pre-PID1 widgets. Free-text `input` on the row folds in as the org/role correction exactly as the W4b `add person` handler specifies. `same as [existing]` stays available (chat / the commitments confirm section) and dispatches per W4b (alias add + `same_as` tombstone); `proposal not relevant` → the `not_relevant` tombstone via the same builder; `snooze proposal 7d` → `chat_dismissal` with `data.snooze_until` = now + 7 days and `data.target_id` = the `person:NNN` row id verbatim (one dismissal per ROW — a snooze quiets the whole cluster row, it adjudicates nothing); **`kind: person_link` (PID1 D4a — a `bp_` row: "Looks like [name] is already on file as [record] — link it?"; the row embeds `data.person_id` = the matched record, `data.alias_name`, `data.cluster_seqs`/`data.cluster_fingerprints`):** `confirm proposal` → `people_writer.add_person_alias(workspace_root, <data.person_id verbatim>, <data.alias_name>)` (skip the alias write when the spelling already equals the record's canonical name), then a `same_as` tombstone per cluster entry (seq or fingerprint, as above), then `brain_proposals.resolve_proposal(..., "applied")`; `dismiss proposal` → `resolve_proposal(..., "declined")` **plus** a `not_relevant` tombstone per cluster entry (the underlying zombie proposals must die permanently — draining them is the point, D4a); `snooze proposal 7d` → standard bp snooze. NEVER dispatch `merge_person_into` from a person_link row — linking is an alias, not a record merge; **`kind: person_merge` (PID1 D4b — two EXISTING records look like one person; the row embeds `data.keep_id` + `data.duplicate_id` verbatim):** `merge person records` → `people_writer.merge_person_into(workspace_root, keep_id=<data.keep_id verbatim>, duplicate_id=<data.duplicate_id verbatim>, source_skill="apply-choices")` then `resolve_proposal(..., "applied")` — CONFIRM-ONLY FOREVER: `merge_person_into` has NO reverser and `person_merge` is never in `AUTO_ALLOWED`; no code path merges records without this click, and the ack says the merge cannot be undone; `proposal not relevant` → `resolve_proposal(..., "declined")` (60d cooldown — the suspect pair re-proposes only if still unresolved after it); `snooze proposal 7d` → standard bp snooze; `kind: org` → `confirm [type]` creates via `org_writer.create_org` (dedup-first through `find_existing_org`), `not relevant` → append `org_proposal_declined` naming the org; `kind: project` → `confirm [type]` routes to workspace-manager's new-project writer, `not relevant` → `chat_dismissal` (60d); `kind: dormancy` → `active` / `archive` / `snooze 14d` per the dont-forget orchestrator's handlers; `kind: schedule_add` → the row is a pointer (registration happens ONLY through the change-schedule / registration add path — never from this dispatcher); `not relevant` → `chat_dismissal` (60d); **`kind: relationship_move` (WG1-B D-B4 — the SCHEDULED staff-meeting "THIS WEEK'S MOVES" row, built by `relationship_moves.moves_rows_from_candidates`; the row embeds `data.person_id` verbatim, `n` = `move:<person_id>`):** `nudge` → the standard compose-on-click chase path above, with the relationship-moves guard first: run `live_contact_check` for the person (the scheduled fire deliberately skipped it — connector-free render; THIS click is where it runs) — un-dormanted with no overdue-commitment carry → honest ack naming the recent touch, NO draft; otherwise compose the opener via the email-writer chain exactly as `relationship-moves/SKILL.md` specifies (voice corpus → Voice Block fallback, draft posture — never auto-sent); `snooze 3d` → `chat_dismissal` with `data.snooze_until` = now + 3 days, `data.target_id` = the row id verbatim AND `data.person_id` = the row's person_id verbatim (the person_id is what `relationship_moves._recently_excluded` keys on, so the snooze holds the person out of next week's compute too); `not relevant` → `chat_dismissal` (60d) carrying the same `target_id` + `person_id` shape. **The `FROM YOUR MEETINGS` section (CAPTUREFLOW §C, 2026-08-01) — rows carrying `confirm` / `drop` / `not mine`, `n` = the commitment's `data.id` verbatim:** these are unconfirmed EXTRACTIONS from the needs-your-call queue, folded into the staff meeting by `needs_review_queue.staff_meeting_group_section`. They dispatch through the queue's OWN write path — the same one the on-demand surface uses — never through `resolve_proposal` and never through a per-surface fork. Collect every row of this section in the batch and dispatch by verb in ONE call each: `confirm` → **`needs_review_queue.confirm_items(workspace_root, <the ids>, source_skill="apply-choices", confirm_weak_ids=<only the wire ids the user named on their own>)`** — that call runs THE shared bulk-accept fence (`watch_gate.screen_bulk_accept`) internally, so a weak row is HELD and reported, never written; "Apply all", a range and a group/call phrase name nothing individually and pass an EMPTY set, and you never re-issue with the held ids on your own. `drop` → `needs_review_queue.drop_items(workspace_root, <the ids>, resolved_by=<user person_id>)`. `not mine` → `needs_review_queue.not_mine_items(workspace_root, <the ids>, resolved_by=<user person_id>)` — the same closure with the honest reason; if the user NAMED the real owner, route to `commitment_state.reassign_commitment` instead. `handler_result` for each row is its own entry in the returned `results`. Ack in the queue's own plain counts (kept / dropped / held), never event-type names, never ids. **Batch undo:** cache the batch's closed ids + dismissal seqs in the fire-marker; a follow-up `undo` in the same chat reverses commitment closes via `commitment_state.reopen_commitment` and mutes via `mute_ledger.clear_dismissals` (the commitment-triage undo shape); brain-batch auto-applied changes reverse via `brain_undo.undo_batch` (additive `brain_change_undone` markers — never edit or delete prior events). Ack in plain change language ("Confirmed 2, declined 1 — the deal moved to negotiating."), never event-type names.)

**To identify which one — dispatch on `src` FIRST (Phase 3 / W4, 2026-07):** every tuple emitted by a current widget carries `src` — the emitting surface's id, stamped by the renderer from the data view's `source_skill`. When the payload's tuples carry `src`, that IS the source: map it to the registry above (`inbox` → `cr-inbox` handlers, `show-my-list` → show-my-list handlers, etc.) and dispatch — no fire-marker read, no time window. A widget click is valid no matter how much later it lands; scheduled chats are persistent threads the CEO opens hours after the fire, so the evening click on the morning widget is the NORMAL case. (Mixed payloads shouldn't occur — one widget, one source — but if tuples disagree on `src`, treat the payload as ambiguous and use the error line below rather than guessing.)

**Fire-marker fallback (legacy widgets only):** when the tuples carry NO `src` (a widget rendered before the src stamp shipped, still live in an old chat thread), fall back to the pre-W4 mechanism — read the most recent fire-marker event whose timestamp is within the last 60 minutes; that event's source field names the source orchestrator. The 60-minute TTL applies ONLY to this fallback path.

### Step 2b — First-run personalization items (`fr*`) dispatch (SPEC FRP1)

Tuples whose `n` starts with `fr` (`fr1` / `fr2` / `fr3`) are **first-run personalization
overrides** from a "Make this yours" block (see `shared/FIRST_RUN_PROTOCOL.md`). They do NOT
route to an orchestrator's item handler. Instead, map each `fr*` tuple to its decision key
and write the override to the SOURCE SKILL's config:

```python
import sys; sys.path.insert(0, "shared/scripts")
from skill_config_writer import get_config, save_skill_config
# source_skill resolved from the fire-marker (the orchestrator's skill); decision key +
# chosen value come from the fr-item's action/sub/input.
cfg = get_config(workspace_root, source_skill, DEFAULTS)
cfg[decision_key] = chosen_value
save_skill_config(workspace_root, source_skill, cfg, is_reconfigure=True, origin="first_fire_override")
```

Then surface the one-line ack the protocol specifies (*"Done — tomorrow's brief runs
full-detail."*) — never echo the payload. `fr*` actions are selection labels, not
`CANONICAL_ACTIONS` verbs, so (like the onboarding Step 1 widget) they are NOT validated
against the canonical-action set. An `fr*` item with an optional `input` (e.g. a VIP-sender
add) passes the typed value through as `chosen_value`.

If the tuples carry no `src` AND no recent fire-marker exists (or the source otherwise can't be determined): surface plain English:

> *"Couldn't tell which chat this belongs to — too much time may have passed. Reply '#N action' for each one, or ask me for that list again and I'll re-show it."*

Don't guess. Ambiguous source = high risk of dispatching the wrong handler.

### Step 3 — Dispatch each {n, action, input?} through the source orchestrator's handler

For each entry in the parsed array, route to the corresponding handler in the source orchestrator's "Reply handling" section.

**Input-bearing action handling:**

| Action pattern | Input shape | Handler uses input as |
|---|---|---|
| `edit then send`, `draft` (consolidated v2.14.4+; was previously two separate verbs) | object `{to, cc, subject, body}` | Override the metadata To/Cc/Subject and `body_lines` with the user's edited values. Then dispatch to the corresponding `send` or `draft` handler. Empty fields preserve the original. |
| `add email then send` | string (a single email address) | DISTINCT handler from `edit then send`/`draft` — does NOT edit the body. Capture the typed address → write a `contact_email_captured` event with `data.person_id` (for downstream people-CRM persistence), set the item's metadata `To:` to that address, and transition the item to an enabled `send`. **On a driver-degraded DELEGATED row (WG1-A D-A4 — the row carries this verb because `nudge` degraded for a no-email owner; there is NO pre-staged body), the nudge body must be COMPOSED first: after the address is captured, run the same `nudge` compose-on-click chain (the `nudge` handler below — email-writer, draft posture) to produce the body, THEN enable `send`. Never transition a body-less row straight to an enabled `send`.** This is the v3.13.8+ Bug #44 recovery verb for a resolved person with no actionable email on file. |
| `edit` (no bracket — non-email contexts only) | string | The full edited body text — replace `body_lines` with this verbatim. |
| `push meeting [date]`, `push to [date]`, `schedule catchup [when]` | string (natural language: "monday at 2", "tomorrow afternoon", "2026-05-12"; for `push to [date]` ALSO a bare number of days — "5", "5 days") | For `push to [date]` run `commitment_state.parse_later_when(<input>, <now_iso>)` first (t3 FB-3: days-from-today and ISO dates resolve deterministically); on `None`, and for the other verbs always, parse the natural-language datetime via the same chrono / date-parser logic the orchestrator uses for free-text user replies. Empty input or unparseable string → surface item-level error in the consolidated ack ("couldn't read 'sometime soon' as a date — try again with a clearer time"). |
| `mark received` (no input — fires `commitment_resolved` directly) | — | No input expected. |
| `mark received from [name]` (MC1 — multi-counterparty per-person receipt) | string | Which recipient delivered — the counterparty's name/id. On a fan-out row the widget embeds the counterparty id, so the input is usually pre-filled from the row's `data-counterparty`. Dispatches `commitment_state.mark_partial_received(commitment_id, counterparty_id=<resolved>/counterparty_name=<free text>)`. The item stays OPEN; when the returned `propose_closure` is true, the ack adds "everyone's received — say close it" (PROPOSE only, never auto-close). |
| `add as person to <Org Name>`, `add as new org <Org Name>` | optional string | The free-text content if user adds a note — record per orchestrator's handler. The specific org name is parsed from the action verb itself (per Rule 5 specific-name patterns). |
| `add context [text]`, `add more context [text]` (back-compat alias) | string | The free-text context to fold into the entity record. |
| `context [text]` (Upcoming Meetings unified affordance, v2.14.37+) | string | Route intent-aware to the meeting handler: question-shaped input → synthesized answer; statement-shaped → call-prep brief regeneration. Use the same heuristic as `shared/CHAT_ACTION_WIDGET.md`: question-shaped if it ends with `?` OR the first word matches `(what\|why\|how\|when\|who\|which\|is\|are\|was\|were\|did\|does\|do\|will\|can\|could\|should\|would)` (case-insensitive); otherwise statement-shaped. |
| `add [text]` (REVIEW affirmative) | optional string | Empty = accept inferred values; non-empty = orchestrator folds the text into the entity record. |
| `decide [text]` (Past Meetings decision-needed) | string | Free-text decision content — written to decision log. |
| `snooze [duration]` (deprecated v2.14.38+ alias) | string | Duration string — back-compat for in-flight pre-v2.14.38 widgets. New widgets emit `snooze 3d` (no input). |
| `reminder push [date]` | string | Natural-language date for the new pin day ("Friday", "next Tuesday", "2026-07-18"). REQUIRED — the widget holds Apply until it's filled (F-17). |
| `fix wording [text]` (S4) | string | The corrected title/summary text, verbatim — REQUIRED. Dispatches `commitment_state.edit_commitment_wording`; empty input holds per the F-17 contract. |
| `reassign to [name]` (S4) | string | The real owner's name — REQUIRED. Resolve via the standard entity-resolve path (ambiguous → disambiguation widget, never guess), then `commitment_state.reassign_commitment(..., confirmed=True)`. |
| `theirs to [name]` (W4b) | string | The real owner's name — REQUIRED. Same handler as `reassign to [name]` (resolve the name, never guess; `confirmed=True` — the typed/tapped name IS the confirmation). |
| `same as [existing]` (W4b) | string | The existing contact's name — REQUIRED. Resolve via the standard entity path (ambiguous → disambiguation widget), then `people_writer.add_person_alias(workspace_root, <resolved person_id>, <the proposal's raw name>)` + the proposal tombstone (see the commitments confirm-section handlers). |
| `add person` (W4b) | optional string | Free-text correction to the proposal's inferred org/role — folds into the Step 3a `create_person` call. Empty = accept inferred values. |
| `split into [items]` (S4) | string | 2+ child titles, split on newlines / semicolons / " / " — REQUIRED. Dispatches `commitment_state.split_commitment`; fewer than 2 parsed titles → item-level error in the ack ("a split needs at least two pieces"). |
| `add subitems [items]` (SUB1) | string | 1+ step titles, same newline / semicolon / " / " parsing — REQUIRED. Dispatches `commitment_state.add_subitems(..., user_confirmed=True)`; the parent stays open. Writer errors (>12 open children; parent is itself a sub-item) surface verbatim in the ack. |
| `confirm [type]` on entity proposal | string (optional) | Free-text corrections to inferred entity details. Empty = accept inferred values. |
| `edit [type]`, `edit [change]` | string | Free-text override of the inferred type / change. |
| `confirm` on entity proposal `[e1/e2]` | string (optional) | Free-text corrections to inferred entity details. Empty = accept inferred values. |

**Specific-name variants route through the generic handlers.** Actions like `add as person to <Specific Org>` and `add as new org <Specific Name>` are NOT separate handlers — they dispatch through the same generic `add as person to <Org Name>` / `add as new org <Org Name>` handler above, with the specific name extracted from the action verb itself (per Rule 5 specific-name patterns) and passed as the resolved target. Do not look for a per-name handler; parse the name out of the label and call the generic path.

If a choice has an action that expects an `input` but none is present, handle it by HOW the verb reached you:

- **Inline box left blank (multi-item ≥5-option rows).** The row carried an input field and the user hit Apply without filling it. Surface plain English in the consolidated ack — DON'T default to a placeholder:

  > *"#N: the box was left blank — add the details and hit Apply again."*

- **Button-dispatched input-bearing verb (WG1-A D-A3).** On a small (≤4-option) row every verb renders as a button with no inline input, so a required-input verb like `theirs to [name]`, `reassign to [name]`, `same as [existing]`, `push to [date]`, `mark lost [reason]` arrives as a bare `{n, action}`. This is BY DESIGN — the button defers the input to you. Do NOT error and do NOT drop it: **PROMPT for the missing value**, naming exactly what's needed (use `verb_taxonomy.required_input_thing(action_id)` — "a name", "a date", "a loss reason", …), then dispatch through the verb's normal handler with the user's answer:

  > *"#N — who's the real owner? (name)"* → resolve the reply, then `commitment_state.reassign_commitment(..., confirmed=True)`.

  A choice that arrives WITH its `input` present dispatches unchanged — this prompt path fires only when the required input is genuinely absent (the byte-exact fully-specified array is never re-prompted).

**Per-action processing:** call each handler exactly once per `{n, action}` tuple, in the order they appear in the payload. If a handler errors mid-batch:
- Log the error to a local accumulator with `{n, action, plain_english_message}`.
- Continue to the next entry. Do NOT abort the whole batch.

### Step 3a — Person-record dispatch contract (v3.2+ MANDATORY)

Some actions create or update person records as a side effect:

- `add [text]` from `cr-past-meetings` REVIEW pending-items (sub-item Na/Nb/Nc) when the inferred entity is a `person` (e.g., `1a add Rio Sample to Summit Company`)
- `confirm` on entity proposal `[e1/e2]` when the proposal is a person
- Any other handler dispatching to people-crm `create_person` / `update_person`

For these, **the dispatch MUST go through `shared/scripts/people_writer.py`. Hand-rolling JSON into `entities.json["people"]` is FORBIDDEN — zero tolerance.** This is the same anti-pattern lineage as the renderer ZERO-MANIPULATION CONTRACT (v2.14.34/.37): when a canonical helper exists, the agent uses it; "improvising the shape because it seems easier" is the bug.

The two real-world failure classes this gate prevents — **wrong field names** and **no dedup** — are memorialized as the person_063 / person_064 incidents (see references/HISTORY.md).

**Dispatch flow** (any apply-time handler that ends in a person create/update):

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from people_writer import create_person, update_person, find_existing_person, DuplicatePersonError, MultipleCandidatesError; print('OK')"
```

If stdout is not exactly `OK`, the apply-time accumulator records `{n, action, "couldn't save that one — try again in a moment"}` and the consolidated ack reports it under `Errors:`. Do NOT fall back to direct `entities.json` edits.

When the import gate passes:

1. **Dedup-first.** Call `find_existing_person(workspace_root, name=<extracted name>, email=<extracted email or None>, aliases=<any aliases the user supplied>)`.
2. **Branch on result — three outcomes (v3.13.7+):**
   - **Existing record (single safe match) → update.** `find_existing_person` returned a dict. Call `update_person(workspace_root, existing["id"], ...)` with whatever new fields the action carried (e.g., `last_interaction`, role correction, notes append). Surface in the consolidated ack: *"#N: Dustin Sample is already in your network — updated."*
   - **No match → create.** `find_existing_person` returned `None`. Call `create_person(workspace_root, canonical_name=..., primary_org_id=..., source_skill=<source_orchestrator>)`. Surface: *"#N: added Rio Sample under Summit Company."*
   - **`MultipleCandidatesError` raised → disambiguation widget.** `find_existing_person` raised because the name was too ambiguous to safely commit (e.g., single-token "Lyra" hit an alias on an existing record but might be a different Lyra). Do NOT auto-pick. Render a disambiguation widget. See "Disambiguation widget shape" below. v3.13.7+ Bug #19 fix.
3. **Catch `DuplicatePersonError` defensively.** If the dedup helper missed a match (rare — usually means the caller didn't pass the matching alias / email), the writer's internal dedup catches it. Treat it as the "existing record" branch and surface the resulting id to the user.

**Disambiguation widget shape (v3.13.7+).** When `MultipleCandidatesError` fires, render a single-item widget per candidate plus a "different person — create new" affordance:

```python
data_view = {
    "widget_mode": "all_batch_widget",
    "header": f"I already know {len(err.candidates)} people with this name. Which one is this?",  # MultipleCandidatesError means ≥2, so "people" always reads right
    "sub_header": f"You're adding {extracted_display_name!r}. Tell me which path to take.",
    "sections": [{
        "title": None,
        "items": [
            # One item per candidate
            *[
                {
                    "n": i + 1,
                    "icon": "👤",
                    "name": c.get("canonical_name", c["id"]),
                    "context_tag": f"already in your contacts — {c.get('role') or '(no role tracked)'}",
                    "body_lines": [
                        f"This is {extracted_display_name}",
                        "  → add this to what I know about them",
                    ],
                    "actions": [f"{i+1} confirm", f"{i+1} skip"],
                }
                for i, c in enumerate(err.candidates)
            ],
            # Plus a final item for "different person, create new"
            {
                "n": len(err.candidates) + 1,
                "icon": "✨",
                "name": f"Add {extracted_display_name} as a different person",
                "context_tag": "different person who happens to share a first name",
                "body_lines": ["This is NOT any of the people above — add them as someone new."],
                "actions": [f"{len(err.candidates) + 1} confirm", f"{len(err.candidates) + 1} skip"],
            },
        ],
    }],
}
```

On the user's click:
- `confirm` on an existing-record item → call `update_person(workspace_root, candidate_id, ...)` with the new context, log `person_disambiguation_resolved` event referencing the original ambiguous capture
- `confirm` on the "create new" item → call `create_person(..., skip_dedup=True, ...)` because the user has explicitly confirmed this is a separate person
- `skip` → no write, log `person_disambiguation_skipped` event so we don't re-ask on the next fire for the same input

Why we route through the user instead of guessing: a silent auto-match corrupts the entity graph (Session-22 Bug #19 — see references/HISTORY.md). The disambiguation widget is the structural defense — entity-graph trust trumps minor UX friction of asking the user once.

**The contract — what NEVER happens at apply time:**

- ❌ Direct write to `entities.json` via `path.write_text()` / `json.dump()` / `open()`
- ❌ Hand-rolled person record dict (regardless of whether it's then "passed to" the helper — the writer's keyword arguments are the ONLY entry surface)
- ❌ Skipping dedup and going straight to create
- ❌ Inventing extra keys (`first_seen_source`, `confidence`, `inferred_from` etc.) on the person record — those go to `events.jsonl`, not the entity

**Self-check:** before any person-write dispatch, ask: "did I import from `people_writer`? did I call `find_existing_person` first?" If either is no → STOP and re-route.

### Step 3b — Auto-org-attribution on person create (v3.13.0+ MANDATORY)

After Step 3a successfully **creates** a person record (existing-record / update path skips this), Step 3b fires the auto-attribution logic. This closes the long-standing gap where new people landed with no `primary_org_id` even when the capture event carried an explicit `org_hint`.

**The dispatch:**

```bash
# (after create_person returned the new person record)
python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from org_writer import attribute_person_to_org
from people_writer import get_person_emails

# 1. Pull the work-domain candidates from the newly-created person
work_domains = []
for e in get_person_emails(new_person_record):
    if '@' in e:
        work_domains.append(e.rsplit('@', 1)[1].strip().lower())

# 2. Pull the org_hint from the original capture event (if any)
# This is the data.org_hint field on the person_pending_review event
# whose seq matches the apply-choices payload's source event.
org_hint = capture_event.get('data', {}).get('org_hint') if capture_event else None

# 3. Try to attach
org_record, reason = attribute_person_to_org(
    workspace_root,
    new_person_record['id'],
    work_domains=work_domains,
    org_hint=org_hint,
    source_skill='apply-choices',
)
print(reason)
"
```

**Branch on the result** (the `reason` string is suitable for the consolidated apply-choices ack):

- *"matched by work-domain {d} to existing org {name}"* → surface in ack: *"#N: filed Sam Sample under Acme Co."* Notify-after, not block-before.
- *"created new org {name} from capture hint and attached"* → surface: *"#N: added Acme Co and filed Sam under it."* No user prompt — the hint was explicit.
- *"no strong signal — left unattached"* → silent. The person record stands with `primary_org_id: null`. The user can attach context later through the existing person-update handlers — e.g. an `add as person to <Org>` action (resolves the org and sets `primary_org_id`) or a `context [text]` note like "she's at Acme / her email is X" on a surfaced item, both of which route to `people_writer.update_person`.

**The contract — what NEVER happens at apply-time auto-attribution:**

- ❌ Auto-attach on a free-mail domain (gmail.com / yahoo.com / icloud.com etc.) — those are personal, not work. The helper filters these.
- ❌ Hand-rolling an org record into entities.json — must go through `org_writer.create_org`.
- ❌ Blocking the apply with a y/n prompt on strong signal. Strong signal = auto-apply with notify-after, per the M decision in the 2026-05-20 handoff. Weak signal = leave unattached (no prompt either; that's the propose-and-confirm flow's job, not this one).

**Failure handling:** if `attribute_person_to_org` raises (e.g., schema validation failure on a corrupted org record), catch it defensively. Log to the apply-choices errors list and continue — the person was still created successfully in Step 3a; missing the org link is a degradation, not a hard fail.

### Step 3c — Intro-followup-check dispatch (v3.13.2+ MANDATORY)

The Pulse intro-followup-check surface (per `orchestrator-dont-forget.md` Phase 4h) emits items with three domain-specific resolution verbs in its action set: `landed`, `didnt land`, `snooze 14d`. The renderer accepts these as canonical, but apply-time needs to write the right lifecycle event for each.

**Where to fetch `person_ids` from at apply-time:** the intro-followup-check item itself only carries `{intro_event_seq, scheduled_for, check_question}` (per intro-broker/SKILL.md). The two person ids the intro connected live on the PARENT `intro_made` event referenced by `intro_event_seq`. Apply-time procedure (v3.13.6+ — defensive across both write shapes):

```python
# Read the parent intro_made event to get the two person ids
import json
events = [json.loads(line) for line in events_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
parent = next((e for e in events if e.get("seq") == intro_event_seq and e.get("type") == "intro_made"), None)
if parent:
    # v3.13.6+ canonical: top-level person_ids array
    person_ids = parent.get("person_ids") or []
    if not person_ids:
        # Pre-v3.13.6 back-compat: scalars under data.person_a_id / data.person_b_id
        data = parent.get("data") or {}
        a = data.get("person_a_id")
        b = data.get("person_b_id")
        person_ids = [pid for pid in (a, b) if pid]
else:
    person_ids = []
```

`person_ids` lives at the TOP level of the event (per `events.schema.json` event shape), not inside `data`. The new events you write follow the same convention (top-level `person_ids` array). The defensive `data.person_a_id` / `data.person_b_id` fallback only matters for in-flight pre-v3.13.6 intro_made events that haven't been re-emitted.

**Dispatch rules:**

- `<n> landed` → append an `intro_landed` event to events.jsonl:
  ```jsonl
  {"type": "intro_landed", "person_ids": [<person_id_a>, <person_id_b>], "data": {"intro_event_seq": <source intro_made seq>, "resolved_by": "operator"}}
  ```
  Use the item's `source_event_seq` (the originating `intro_made` event's seq); the two person ids come from the parent event per the lookup above. The event closes the intro-followup-check loop — the intro counts as successful in relationship-graph queries and downstream intro-broker voice-sample logic.

- `<n> didnt land` → append an `intro_didnt_land` event, same shape (substitute the type):
  ```jsonl
  {"type": "intro_didnt_land", "person_ids": [<person_id_a>, <person_id_b>], "data": {"intro_event_seq": <source intro_made seq>, "resolved_by": "operator"}}
  ```
  Useful signal for intro-broker's pattern detection — over time, "this counterparty type doesn't respond to this framing" becomes learnable.

- `<n> snooze 14d` → schedule a re-emit. Append an `intro_followup_check` event (NOT a `chat_dismissal`):
  ```jsonl
  {"type": "intro_followup_check", "person_ids": [<person_id_a>, <person_id_b>], "data": {"intro_event_seq": <source intro_made seq>, "scheduled_for": "<today + 14 days>"}}
  ```
  Pulse's next fire re-checks `scheduled_for` against today's date and re-surfaces the item when due. This differs from a standard 24h `chat_dismissal` — the snooze is a deliberate "check back in two weeks", not a "not now".

- `<n> skip` — standard `chat_dismissal` event (24-hour TTL), same as every other surface. The intro-followup-check re-surfaces tomorrow.

**Consolidated ack lines** (Step 4B surface) — one line per item:
- *"#N: marked Sam & Bo's intro as landed."*
- *"#N: logged Sam & Bo's intro as didn't land."*
- *"#N: snoozed Sam & Bo's intro check for 14 days."*
- (Skip: silent.)

**Why this lives in apply-choices** (not in the orchestrator's reply handler): apply-choices is the single dispatcher for ALL widget Apply submissions. The orchestrator-dont-forget Phase 4h surface emits the items; apply-choices owns the resolution. Putting handler logic in the orchestrator would duplicate the dispatch pattern + drift over time.

### Step 3d — Voice-correction capture (B1 — mandatory on email actions)

On any `send` / `edit then send` / `draft` for an email-writer-family draft, capture the voice signal — silently, never blocking the send. This includes the t3 FB-10 inline-body edit: a choice whose `input` is `{"body": ...}` came from M typing directly into the rendered card, and the rendered-vs-queued diff lands in the corrections log exactly like an `edit then send` edit:

```python
import sys; sys.path.insert(0, "shared/scripts")
from voice_corrections import snapshot_draft, diff_and_classify, append_correction

# (a) snapshot the canonical (pre-edit) body alongside the email_drafted append.
#     native_draft_id takes the DECLARED BACKEND's native draft id (the same
#     value build_email_drafted_provenance now writes onto the event under
#     native_draft_id; EW2+T F-12 / FB-plumbing item 5 — a Superhuman draft id
#     goes here too, and the legacy gmail_draft_id kwarg still works).
snapshot_draft("<abs workspace root>", skill="email-writer", domain="<email domain>",
               recipient_id="<person_id or None>", recipient_email="<addr>",
               subject="<subject>", body="<canonical body>",
               draft_event_seq=<the email_drafted seq>, native_draft_id="<backend-native draft id or None>")

# (b) if the action's input.body differs from the canonical body, classify + append
for r in diff_and_classify("<canonical body>", "<input.body>"):
    append_correction("<abs workspace root>", skill="email-writer", domain="<email domain>",
                      recipient_id="<person_id or None>", original=r["original"],
                      corrected=r["corrected"], correction_type=r["correction_type"],
                      notes=r["notes"], draft_event_seq=<seq>)
```

The helper strips leading `> ` quote lines before comparing (so a quoted reply never self-reports as an edit). Multi-draft widgets (n>1): snapshot + diff per item, keyed by that item's `draft_event_seq`. On any helper error, swallow and log to the apply-time errors accumulator — voice capture never blocks a send (no chat narration either).

### Step 3e — Inbox triage-feedback capture (Phase 6 Loop 1 — mandatory on inbox actions)

On any `send` / `edit then send` / `draft` / `skip` for a `cr-inbox`-sourced item, capture the triage signal — silently, never blocking dispatch. The inbox orchestrator cached the per-item context at render time (sender, domain, the Phase-5 `bucket_assigned` handling label, whether a draft was offered — see `orchestrator-inbox.md` Phase 8); read that cache the same way the other handlers read the recipient/subject cache.

```python
import sys; sys.path.insert(0, "shared/scripts")
from event_gate import append_event
from triage_feedback import build_triage_feedback_event

ev = build_triage_feedback_event(
    sender="<cached sender address>", domain="<cached domain or None>",
    bucket_assigned="<cached Phase-5 label: surfaced | noise:<subcat> | fyi>",
    action_taken="<the canonical inbox verb the CEO clicked>",
    draft_offered=<True if a draft was rendered for this item>)
append_event("<abs workspace root>/_hq/data/events.jsonl", [ev], holder="apply-choices.triage_feedback")
```

This is the ONLY new capture Loop 1 needs — it is the strongest triage-relevance signal in the product, and pre-Phase-6 it was discarded. insight-generator Pass 13 mines a 30-day window of these to propose sender-priority rules. On any helper/gate error, swallow and log to the apply-time errors accumulator — capture never blocks the inbox action.

### Step 3f — Dismissal fingerprint (Phase 6 Loop 2 — stamp on every chat_dismissal)

Every `skip` on ANY surface writes a `chat_dismissal` (24h TTL) — unchanged. Phase 6 additionally stamps the Loop-2 suppression identity so a repeated "no" becomes learnable. When writing a `chat_dismissal`, set `data.fingerprint` (+ the derived triple) from the source orchestrator's cached item identity:

```python
from surface_preferences import dismissal_fingerprint
fp = dismissal_fingerprint(surface="<cr-inbox|cr-commitments|pulse|...>",
                           item_class="<chase|stale_project|newsletter|dormancy|...>",
                           entity_id="<person_/project_/org_ id, or None for class-wide>")
# chat_dismissal payload gains: data.surface, data.item_class, data.entity_id, data.fingerprint
```

`entity_id` uses the resolved canonical id, NEVER a per-render event seq (a seq varies every day and can never recur — the fingerprint would be useless). insight-generator Pass 14 mines fingerprints dismissed 3+ times in 30 days across BOTH `chat_dismissal` and `dont_forget_feedback`. Legacy dismissals without these fields are handled read-side by `surface_preferences.normalize_dismissal` (derive-best-effort), so this is purely additive — no migration.

**TTL stamp (v4.6.0 S4 — the mute ledger needs exact expiries):** every `chat_dismissal` ALSO sets `data.snooze_until = <now + verb_taxonomy.mute_ttl_days(action)>` — the taxonomy row's TTL, the same number the button label states (1d for `skip`/`skip all`, 3/7/14d for the snoozes, 60d for `not relevant`). Pre-S4 only the longer snoozes carried `snooze_until` and 24h skips relied on the legacy default; stamping it uniformly makes `show muted` render every mute's remaining time exactly. Readers are unchanged (`snooze_until` has been the first-checked field since v3.5.0).

**Duration in the ack (S4 — the F-59 rule extended to apply time):** when the batch contains a mute whose TTL is 3 days or longer, the consolidated ack states the duration and the way back, e.g. *"#N muted for 60 days — say `show muted` to bring it back early."* The 60-day `not relevant` mute ALWAYS gets this line (S2 put the duration on the button; the ack repeats it at click time). 1-day snoozes stay silent (tomorrow is self-explanatory).

### Step 3g — The `hold` verb + parking items IN CHAT (SPEC FB-19 — M's ruling 2026-07-16)

**Why this exists.** On 2026-07-16 two queue rows were parked in chat ("hold those two, I'll think about it") and re-rendered verbatim on the very next fire — because "I'll think about it" had nowhere to live. The ledger only understood clicked snoozes. A surface that re-asks a question the user just answered reads as not listening, and it is the fastest way to teach someone to stop reading the card at all. Now that the staff meeting is the SOLE adjudication surface (FB-20 made the brief read-only), that card has to be worth reading every time.

**The writer — one call, never a hand-rolled event:**

```python
from mute_ledger import hold_item
hold_item(WORKSPACE_ROOT, "<row id verbatim>", source_skill="apply-choices",
          surface="<staff-meeting|cr-brain>")   # 14d default (HOLD_TTL_DAYS)
```

It writes a `chat_dismissal` with `data.reason = "held"` and `data.snooze_until = now + 14d`. No new event type, no new reader: every liveness path already honors it (`live_mutes`, `active_dismissal_target_ids`, `brain_proposals.load_open_proposals`). That also means the morning brief's pointer count and the staff-meeting card agree about held items **by construction** — both read the same filtered projector, so a held row is neither rendered nor counted.

**Two paths reach it, and BOTH are honored:**

1. **The `hold` verb** on a row's dropdown (label: *"Hold — parked till you answer (14 days)"* — UXR1 D7a differentiated it from Snooze by intent; the F-59 rule still holds: the label states its own duration). Dispatch as above.
2. **A plain-English park in chat** — *"hold those two"*, *"park that for now"*, *"leave those, I'll think about it"*, *"skip these for today, I need to decide"*. Resolve which rows the user means from the rendered `display_n` numbers or the names they used, then call `hold_item` once per row. **This path is not optional and not a nicety** — it is the exact live failure. If you are unsure WHICH rows they meant, ask; do not silently hold nothing.

**A hold never outlives its own question.** The moment the item IS answered on any surface (`confirm` / `not relevant` / the underlying commitment closes), clear the hold with `mute_ledger.clear_dismissal` so the ledger doesn't carry a stale park. A hold is a promise to ask again later, not a delete.

**The row that offers holding SAYS so** — the verb label carries the duration, and the ack states it: *"Held #2 and #3 for 14 days — say `show muted` to bring them back sooner."* (Step 3f's duration-in-the-ack rule; a hold is ≥3 days so it always gets the line.) Never ack a hold as "dismissed" or "skipped" — the user parked it to decide, and telling them you dropped it is both wrong and alarming.

**Idempotent:** holding an already-held item returns `{"status": "already_held"}` and writes nothing — a user repeating themselves must never silently extend the clock behind their back.

### Step 4 — Surface ONE consolidated chat turn (v2.14.0+ — TERMINAL vs DRAFT-PRODUCING split)

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "#N: I see 2 person record(s) with this name — update this record with the new context."
- GOOD: "#N: I already know 2 people with this name — which one is this?"

**Critical distinction (v2.14.0):** actions split into two categories. The category determines the response surface.

**TERMINAL actions** — user has already decided. Fire immediately, confirm and stop.
- `send` — fire send right now. Body precedence: the choice's `input` object when present (an `edit then send` mfe payload OR the t3 FB-10 inline-body edit `{"body": ...}` — the widget serialized the CURRENT on-screen text, so what queued IS what M saw), else the canonical email body.
- `edit then send` — apply input object, then fire send (ONE round, NOT two clicks). Per M's v2.13.2 testing: *"if you hit edit send — then make edits and hit apply, it should trigger the email sent without regenerating in response."*
- `draft` (consolidated v2.14.4+; was previously two separate verbs) — apply the `input` object if present (mfe payload or the t3 FB-10 inline-body `{"body": ...}`), save the body to the declared backend's Drafts immediately via the seam-resolved draft tool (§0.5) + append `email_drafted` per the Step 4B substrate-writes block (EW2+T, F-12)
- `mark received`, `mark received all`, `mark done`, `mark paused`, `resolved`, `resolved [reason]`, `archive`, `keep paused`, `active` — fire the state change immediately. **Commitment-class closures (Stage B 2026-07 — REQUIRED):** whenever the state change closes a COMMITMENT (a `resolved` / `mark done` / `mark received` on a commitment or commitment_to_discuss item), the write goes through `commitment_state.close_commitment(workspace_root, <data.id from the widget verbatim>, resolved_by=<user person_id>, evidence=<short reason>, source_skill="apply-choices", user_confirmed=True, resolution="done")` — never a hand-built `commitment_resolved` append. It normalizes legacy ids, refuses no-match ids loudly (`CommitmentIdError` → ack the item as "couldn't find that one" instead of writing an orphan tombstone), and is idempotent over the full resolved-id set. **already_resolved is a NO-OP, not a failure and not a reason to improvise (v4.5.2 R1c):** when `close_commitment` returns status `already_resolved`, write NOTHING for that item — no second tombstone, no hand-built `commitment_resolved`, ever (83 duplicate tombstones in live history came from blind re-closes). Record the item in the audit event with `outcome: "already_resolved"` and acknowledge it honestly in the consolidated ack ("that one was already closed — nothing to change"), counted separately from the applied items. `mark received` closes with `evidence="counterparty delivered — marked received"`. **v3.13.8+ cascade-close (Bug #51):** when the resolved item is a `commitment_to_discuss` wrapper from show-my-list (the source orchestrator's `kind` was `list`), look up `data.source_event_seq` on the wrapper. If it points at an unresolved `commitment` event, ALSO close the source via a second `close_commitment(workspace_root, <source_seq>, ..., extra_data={"resolved_via_wrapper_seq": <wrapper_seq>})` call — the normalizer maps the bare seq to the canonical id. Surface to the user: *"Cleared the [item label] — and closed the underlying commitment."* Skip the cascade only if the source_event_seq points at a non-commitment event type (close_commitment's `CommitmentIdError` / `already_resolved` return tells you — e.g., a `pending_review` or a previously-resolved commitment).
- `confirm`, `confirm [type]` — apply the proposed change immediately
- `escalate to memo` — fire memo-writer (it produces a memo .docx — see DRAFT-PRODUCING handling for the resulting doc)
- `accept`, `decline`, `decline [reason]` — fire the calendar response immediately
- `skip`, `skip all` — silent dismissal, no widget
- `snooze [duration]`, `snooze 3d`, `snooze 14d`, `keep`, `add to my list`, `not relevant` — record the state, no widget. For `add to my list` (RETIRED verb, MLK1 2026-07-21 — no new render offers it; this dispatch exists ONLY for persisted old widgets, and the click keeps its ORIGINAL meaning, never aliased to a different action): when the tuple carries NO `context`, write a `commitment_to_discuss` event to events.jsonl with `data.source_event_seq` pointing back to the originating item's source, exactly as before — an acceptable fossil trickle; the item drains later via `show my list`. When the tuple DOES carry `context`, it is the orphan-note carrier — route it through the re-route block below, NOT through a list write. For `snooze 14d`: see "Intro-followup-check dispatch" below — this verb also schedules a future re-emit, not just a dismissal.
- `add to my plate` → commitment_state.create_personal_task(workspace_root,
  title=<row title>, owner_id=<user person_id>, source_ref=<originating item source>,
  source_event_seq=<originating event seq>, source_skill="apply-choices").
  Ack (plain): "Added to My Plate — it's a task you own now; you'll see it on My Plate."
  It surfaces on the next My Plate fire / `show my plate`.
- `landed`, `didnt land` — intro-followup-check resolution verbs (v3.13.2+). Each writes a domain-specific lifecycle event. See "Intro-followup-check dispatch" subsection below.
- `show why` (WG1-A D-A6) — the row-quarantine placeholder's only verb, read-only. The row it replaced was withheld because its RENDERED content failed a per-row leak scan (an internal id/token, or a non-workspace path, reached visible text). Respond in plain English that names the SOURCE so the user can fix the underlying data: the withheld row's `n` / source event and the defect class the placeholder already showed — e.g. *"Row 2 was held back because its text carried an internal id (the record behind it stored a raw org token) — fix that record and it renders next time."* NEVER echo the offending string itself, write no substrate event, and render no widget.
- `fix wording [text]`, `reassign to [name]`, `split into [items]`, `add subitems [items]`, `unmute` — S4/SUB1 lifecycle verbs, all terminal. Each fires its `commitment_state` / `mute_ledger` writer immediately (see the commitment-triage and show-my-list source entries in Step 2 for the exact calls) and confirms in plain English — the corrected line, the new owner, the N new items (or N new sub-items under the still-open parent), or when the unmuted item re-surfaces. No widget re-render. One cascade exception: `resolved` on a parent with open sub-items is NOT terminal until the user answers the one-line cascade confirm (Step 2's commitment-triage entry) — never close a family silently.
- `mark received from [name]` — MC1 per-person receipt (v4.6.0), terminal. Dispatch `commitment_state.mark_partial_received(workspace_root, <commitment_id>, received_by=<user id>, source_skill="apply-choices", counterparty_id=<resolved id from the row's embedded `data-counterparty`>` OR `counterparty_name=<free text when unresolved>)`. The item stays OPEN — this is a receipt, not a closure. Ack names who was marked and how many remain ("Marked Priya received — 1 of 3 board members left"); when the return's `propose_closure` is true, add the closure PROPOSAL to the ack ("everyone's received — say close it to close it out"). NEVER auto-close and NEVER stage a chase to a received counterparty.
- `mine`, `theirs to [name]`, `merge`, `keep both`, `add person`, `same as [existing]`, `proposal not relevant` — W4b confirm-section verbs (v4.6.1), all terminal. Dispatched per `orchestrator-commitments.md` § "Confirm section actions": `mine` → `commitment_state.confirm_commitment_owner`; `theirs to [name]` → `commitment_state.reassign_commitment(..., confirmed=True)` after the standard name-resolve; `merge` → `commitment_state.supersede_commitment(..., user_confirmed=True)` (survivor = the row's flagged duplicate target); `keep both` → `commitment_state.clear_review_flags`; the three person verbs run their entity write (Step 3a create / `people_writer.add_person_alias` / nothing) and then append the proposal tombstone via `confirm_flow.build_person_proposal_resolved_event` + `event_gate.append_event` — the tombstone is what stops the proposal re-surfacing, so it is never skipped, even for `proposal not relevant`. Acks in plain English; never event-type names. Guardrail restated: none of these ever stage or send a chase email — confirmation changes state, chase happens on later fires against CONFIRMED items only.

  **Orphan-note re-route (MLK1 D2, 2026-07-21 — supersedes the v3.13.0 list capture):** when the widget's `+ Add context` field had text but no action button was selected on that item, the renderer synthesizes a fallback `{n, action: "add to my list", context: "<typed text>"}` choice (see `chat_output_renderer.py` `crApplyAll` orphan-note capture — the carrier deliberately keeps the legacy wire id so old and new widgets emit one shape). The list is retired, so the capture NEVER writes a `commitment_to_discuss`. Route every context-bearing tuple of this shape through the canonical helper:

  ```python
  from orphan_note import reroute_orphan_note
  result = reroute_orphan_note(WORKSPACE_ROOT, <context text>, <originating item's source_event_seq>, source_skill="apply-choices")
  ```

  THREE outcomes, all honest: `{"outcome": "noted", ...}` — the source item resolved to a person or thread, and the helper appended ONE `note` event there (`data.via: "orphan_note_capture"`, `data.summary` = the typed text; gated writer inside the call) — ack in plain English ("Held that as a note on [name]."). `{"outcome": "already_noted", ...}` — this exact note (same target, same source row, same text) was already on disk and NOTHING was written; the helper is idempotent on identity (DOGFIX1 2026-07-27 — a re-dispatched payload wrote four notes twice). Ack it as the no-op it is ("Already held that one on [name]." / fold it into a consolidated count), NEVER as a fresh capture — a doubled ack on an undoubled write is the same lie in the other direction. `{"outcome": "declined", "line": ...}` — nothing resolved and NOTHING was written; surface the helper's `line` verbatim in the ack ("I need a person or project to pin that to — say more and I'll hold it properly"). A context-free fragment saved nowhere is better than a junk drawer — never fall back to a list write, and never guess a target the helper didn't resolve.

  **Re-dispatch is safe, and stays the helper's job.** If you are unsure whether the note step already ran for this payload, call the helper again rather than reasoning about it: a repeat returns `already_noted` and writes nothing. Do NOT pre-check the substrate by hand, and do NOT skip the call — a hand-rolled "did I already do this" read is exactly the improvisation that produced the double write.

  When the choice carries NO `context` field (the user selected an action without typing a note), keep the legacy behavior: the chosen action dispatches normally. When the choice carries BOTH a `context` AND was paired with an action via the happy path (the user typed a note AND clicked an action — they go together), the note is stored as `data.note` on whatever event the chosen action produces (`commitment_resolved`, `chat_dismissal`, etc.). One deliberate exception: for the retired `add to my list` wire id, ANY context-bearing tuple routes through the re-route above — the carrier shape and an old explicit click with a typed note are indistinguishable on the wire, and no new list write is ever acceptable. The `context` field is the user's typed note no matter which path was used.

For terminal actions, the response is a SHORT plain-English ack (Step 4B below) — no widget re-render, no second-click required.

**DRAFT-PRODUCING actions** — user requested generation of a fresh artifact for review.
- `push meeting [date]` — generate reschedule email draft. (`push to [date]` on a MEETING-shaped item routes here too; on commitment-family sources it is TERMINAL — the t3 FB-3 Later… auto-route in Step 2's commitment-triage entry — and never generates a reschedule draft.)
- `draft re-engagement` — generate re-engagement email draft
- `nudge` (WG1-A D-A4) — the delegated Waiting On row's chase, compose-on-CLICK. Generate the nudge email draft through the email-writer chain (draft posture — nothing touches Superhuman until the user acts, never auto-sent), the same path as `draft re-engagement`, using the delegated item + counterparty context the row carries. Composed HERE at apply time, never at widget render, so scheduled fires that offered the button stay connector-free. If the counterparty has no actionable email on file, fall back to the `add email then send` recovery flow rather than erroring.
- `follow-up call` — generate calendar-invite request draft
- `status check` — generate status-check email draft
- `propose [time]` — generate proposed-time email draft
- `schedule catchup [when]` — generate catchup-request email draft
- `context [text]` (v2.14.37+) — single unified context affordance for upcoming-meetings items. Intent-aware dispatch on the user's textarea content:
  - **Question-shaped** (text ends with `?` OR first word matches `^(what|why|how|when|who|which|is|are|was|were|did|does|do|will|can|could|should|would)\b` case-insensitive) → synthesize an answer using prior meeting transcripts + recent email threads with attendees + relevant decision-log entries. Returns 1-3 paragraphs in chat with source citations (meeting dates, email subjects). Routed via Step 4B (terminal-style ack — the answer text IS the response). Replaces the v2.14.14 `ask question [text]` handler.
  - **Otherwise (statement / instruction)** → re-run call-prep with the user's added context folded in. Regenerate the `.docx` brief via the docx skill. Routed via Step 4A (draft-producing — fresh brief link surfaces in the apply-time widget). Replaces the v2.12.4 `add more context [text]` handler.
- `add more context [text]` (v2.12.4 - v2.14.36 alias, accepted for back-compat) — translate to `context [text]` and dispatch through the intent-aware handler above. Pre-v2.14.37 widgets in flight at upgrade time will still apply correctly.
- `ask question [text]` (v2.14.14 - v2.14.36 alias, accepted for back-compat) — translate to `context [text]` and dispatch through the intent-aware handler above.
- `prep deep work` — generate context-loaded prompt (text content for review, not a send)
- `investigate` — generate cross-reference report (text content)

For draft-producing actions, the GENERATED CONTENT surfaces in a NEW widget so the user can review/edit/send each draft. Step 4A below.

**Mixed batch** (some terminal, some draft-producing): plain-English ack at top summarizing terminal outcomes, then the widget for drafts. Step 4C below.

#### A. Draft-producing actions only → emit a NEW widget

Per M's Apr 30 ask: *"You can host all of those in the same widget."* All draft-producing outputs go in ONE widget — never N separate widgets.

**Step 4A.1 — verify renderer imports (v2.13.0+ MANDATORY, bash-gated, full validator chain):**

Before producing the consolidated response, you MUST execute:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from chat_output_renderer import render_chat_output_widget, validate_chat_output, CANONICAL_ACTIONS, CanonicalActionError, LeakDetectedError; print('OK')"
```

If stdout is not exactly `OK`, ABORT and surface plain English: `(Couldn't put your drafts together just now — try again in a moment.)` Do NOT fall back to markdown rendering of the drafts. The widget IS the contract.

**v2.13.0 enforcement parity with orchestrators:** the renderer raises `CanonicalActionError` if the apply-time data view has a non-canonical action verb (e.g., the agent improvising `keep as draft` instead of `draft`, `send as is` instead of `send`, or numbered markdown actions like `send 1`). It raises `LeakDetectedError` if the rendered output contains forbidden patterns (per `shared/CONTRACT.md` Rule 4). Both blocking; no silent fallback to markdown.

**Action set (frozen — FB-17 form):** the apply-time post-Apply widget for email drafts uses ONLY these action verbs (matching `CANONICAL_ACTIONS`):
- `<n> send`
- `<n> draft`
- `<n> snooze 3d`

(FB-17, M 2026-07-19: the email card is Send / Draft / Snooze (3 days), no dropdown. `edit then send` RETIRED — the FB-10 inline-editable body replaced the popup editor; the wire id stays a dispatchable deprecated alias → `send` for in-flight widgets, but no new card emits it. Pre-v2.14.4 the set was `send` / `edit then send` / `to drafts` / `edit then draft` / `skip`; `to drafts` + `edit then draft` were consolidated into `draft`.)

NOT allowed (rejected by validator): `keep as draft`, `send as is`, `revise`, bare `edit`, `regenerate`, `draft 1` / numbered-prefix shorthand, the legacy `to drafts` / `edit then draft`. If the agent is tempted to use any of these — STOP. Pick from the canonical set.

**Step 4A.2 — Build the consolidated post-Apply data view (single widget for ALL drafts/docs):**

```python
post_apply_data_view = {
    "widget_mode": "all_batch_widget",
    "header": "<plain-English summary, e.g. '3 drafts ready. 1 brief regenerated.'>",
    "sub_header": None,
    "sections": [{
        "title": None,
        "count": None,
        "items": [
            # ONE item per draft AND per regenerated document. Multiple drafts go in the
            # SAME widget — DO NOT emit separate widgets per draft. Per M's ask: "You can
            # host all of those in the same widget."
            {
                "n": 1,                       # NEW numbering for this widget — not the source #N
                "icon": "✉",
                "name": "<recipient name>",
                "subject": "<subject>",
                "context_tag": "draft from #<source_n>",
                "metadata": [("To", "<email>"), ("Subject", "<subject>")],
                "body_lines": [...],
                "actions": ["1 send", "1 draft", "1 snooze 3d"],
                "original_thread": {...} or omit  # if the draft has a source thread
            },
            {
                "n": 2,
                "icon": "📄",
                "name": "<doc title>",
                "context_tag": "regenerated from #<source_n>",
                "artifact_link": {"label": "Open brief", "url": "<docx url>"},
                "actions": ["2 skip"]   # docs are read-only — only action is dismiss
            },
        ]
    }],
}
```

**Canonical email action set** (use these EXACTLY — do not improvise alternatives like `keep as draft`, `send as is`, `revise`):

| Action | Display | Behavior |
|---|---|---|
| `<n> send` | Send | Send via the seam-resolved backend per `EMAIL_DRAFT_PROTOCOL.md` §0.5 (Gmail backend → the §3c ladder: Zapier → native threaded → standalone; Superhuman/Outlook → native send). Appends `email_drafted` + `email_sent` (F-12). |
| `<n> draft` | Draft | Save to the declared backend's Drafts via the seam-resolved draft tool (§0.5); the card body is inline-editable (FB-10), so an edit rides the choice as `{"body": …}`. Appends `email_drafted` (F-12). v2.14.4+ consolidated `to drafts` + `edit then draft` into this single verb. |
| `<n> snooze 3d` | Snooze (3 days) | FB-17 — "deal with it later": mute the card for 3 days via the mute ledger, then it resurfaces. No connector write. |
| `<n> edit then send` *(retired FB-17)* | — | Deprecated alias → `send`. Accepted from in-flight widgets; the apply-time handler still applies its `{to,cc,subject,body}` payload then sends. Never emitted anew — the inline body (FB-10) is the edit surface now. |

NOT allowed: `keep as draft` (use `draft`), `send as is` (use `send`), `regenerate` (re-fire the source orchestrator instead), bare `edit` (use `draft`), the legacy `to drafts` / `edit then draft` verbs (consolidated v2.14.4+).

**Step 4A.3 — render and post:**

Render + persist via the widget_code transport (EW2+T, F-15): `widget_transport.render_and_persist(data_view=post_apply_data_view, wrapper="fragment", persist_dir=<WORKSPACE>/_hq/.system/widgets, name_hint="apply-choices")`, then pass `transport["html"]` (the persisted page's validated bytes, verbatim) to `mcp__visualize__show_widget` as `widget_code`. Never hand-compose or post-process the HTML. The widget IS the post — do NOT compose a markdown summary of what's in the widget. Same posting contract as the source orchestrators (`shared/CHAT_ACTION_WIDGET.md` § Transport).

**Step 4A.4 — Post the chat-links section:**

After the widget, emit a SHORT markdown chat-links section per `_hq/CONVENTIONS_SOURCE_LINKS.md`:
- For each draft with an original thread → `[<recipient> — <subject>](<thread URL>)`
- For each regenerated doc → `[<doc title>](<docx URL>)`

Numbering matches the widget items.

**Step 4A.5 — STOP.** No trailing narration. No "I drafted X for Y because Z." No "Wrote audit event to events.jsonl." No commentary on which actions you ran or what the response handlers did. Per `shared/CHAT_ACTION_WIDGET.md` MUST NOT rule #5: the widget + Links is the entire post. (v2.12.5+)

#### B. Terminal actions only → emit a SHORT plain-English ack (NO widget re-render)

Per M's v2.13.2 ask: *"if you hit edit send — then make edits and hit apply, it should trigger the email sent without regenerating in response."* Terminal actions FIRE immediately. The response is a short confirmation, not a widget for a second click.

Specifically for `send` / `edit then send` / `draft` (consolidated v2.14.4+; was previously two separate verbs):
- Take the canonical body OR the user-edited body (from `input` object) — never re-surface the body for review.
- Fire the actual send / save through the seam-resolved tool on the declared mail backend per `EMAIL_DRAFT_PROTOCOL.md` §0.5 (on a Gmail backend that resolves to the §3c ladder — Zapier first if configured, native threaded, standalone; a Superhuman or Outlook backend sends/drafts natively, no Zapier leg).
- **Email substrate writes — EVERY backend, MANDATORY at click time (EW2+T, F-12):** the backend changes WHICH tool created the draft, never WHETHER the substrate learns about it. In the same motion as the connector write:
  - `send` / `edit then send` → append `email_drafted` THEN `email_sent` (`source_skill` stays the authoring skill; `draft_event_seq` on the sent event links the pair). Draft identity via `connector_adapters.provenance.build_email_drafted_provenance(draft_id=…, provider=…, server_id=…, address=…)`; send identity via `build_email_sent_provenance(…)`. Never hand-write the id field names — the builders own the spelling, and a Superhuman draft/message id rides the same fields a Gmail one does.
  - `draft` → append `email_drafted` (builder fragment as above; no `email_sent` yet).
  - Step 3d's voice snapshot fires alongside these on every backend — its draft-id argument is the builder's native id.
  - Failure posture: if the connector write succeeded but an event append fails, record it in the apply-time errors accumulator — NEVER silently skip the writes (the F-12 live shape was exactly a connector draft with no substrate trace: drafted-but-not-sent detection, send linkage, and voice learning all went blind).
- **CRU pass (v2.14.6+, send only):** after the send is confirmed by the connector, run the cross-reference helper to detect whether this send fulfilled an open commitment owned by the user. See "CRU pass" section below for the exact bash flow. Silent — no chat narration regardless of outcome.
- Confirm with `✓ Sent at HH:MM — Re: <subject> → <recipient>` (one line per send) or `✓ Saved to Drafts — Re: <subject> → <recipient>`.
- NO widget. NO second click. ONE round.

##### CRU pass (v2.14.6+ — silent commitment auto-resolution on `send`)

Per `shared/scripts/cru_match.py` Path 1. Runs ONLY when a `send` action just fired successfully (`edit then send` counts; `draft` does NOT — drafts aren't fulfillment). Conservative auto-resolve only; borderline matches go to a `pending_review` queue surfaced in the next Pulse fire.

Skip entirely if any of:
- The send failed (no commitment can have been resolved by a failed send).
- Sender's `person_id` cannot be resolved from entities.json (the user record has `is_user: true` or `is_primary_user: true` — pull that record's `person_id`).
- No open commitments exist where the user is the owner (helper returns `[]`).

**Capture the SENT message's native id from the connector's send response before running anything below (RECONFENCE F-8).** The layer-1 fence needs the ref of the message being scored, and this snippet cannot invent it — a snippet that references a value the prose never told the model to capture is precisely the failure TRAINFIX was built to close. The same connector response that confirms the send carries the sent message's id; hold it as `sent_message_id`. It is the id `build_email_sent_provenance` was handed a moment earlier, so it is already in hand.

**And it must be the SENT id, never the DRAFT id.** A draft identifier never resolves to a real thread, so a ref built from one is a dead anchor: it fences nothing while looking like a fence. That was measured live — two real sends recorded under `draft…` ids, and the reply watch went blind on both. **If the response gives you only a draft id, OMIT `send_source_ref` entirely** and record it in the apply-time errors accumulator. Layer 1 inert-and-recorded beats layer 1 present-and-lying, and the caller-side self-closure check plus layer 3 still stand.

Otherwise, per send, execute via bash:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from cru_match import (
    load_open_commitments,
    match_send_to_commitments,
    build_pending_review_event,
)
from commitment_state import close_commitment, mark_partial_received, CommitmentIdError, PendingReviewError
from connector_adapters.provenance import primary_artifact_key, resolve_mail_provider
from atomic_write import atomic_append_jsonl

workspace_root = '<absolute path to the workspace root>'
events_path = '<absolute path to _hq/data/events.jsonl>'
opens = load_open_commitments(events_path)

# MAILSEAM: resolve the mail provider ONCE, from the workspace's DECLARED email
# backend. Never a literal — `gmail:<id>` built on a Superhuman backend produces
# a key that matches no commitment on disk, which is a fence that stopped
# fencing without failing anything (BUG-3719). None stays None: the identity
# helpers then compare native ids rather than inventing a label.
provider = resolve_mail_provider(workspace_root)

# The SENT message's native id, captured from the connector's send response
# per the paragraph above. A DRAFT id here is a dead anchor — omit
# send_source_ref entirely instead of building a ref from one.
sent_message_id = '<the sent message id from the connector response>'

results = match_send_to_commitments(
    open_commitments=opens,
    sender_person_id='<resolved sender person_id>',
    recipient_person_ids=['<recipient person_id 1>', ...],
    subject='<subject of the send>',
    body='<body of the send>',
    # RECONFENCE layer 1 — the ref of the message being scored. A commitment
    # attributed to THIS message is dropped BEFORE scoring: the send that
    # captured a promise is not evidence that the promise was kept. This path
    # needs it as much as the scheduled rail does — the send that just went out
    # through this very Apply is exactly the send a capture phase can log a
    # promise from moments later. Built through `primary_artifact_key` so one
    # artifact never reads as two across a backend switch.
    send_source_ref=primary_artifact_key(provider, sent_message_id),
    # RECONFENCE layer 2 (`exclude_captured_since`) — DELIBERATELY NOT PASSED,
    # and this is the one call site where that is the correct answer. It excludes
    # commitments captured at
    # or after the START OF THIS FIRE, and an in-product send has no batch fire:
    # no capture phase runs beside it and there is no fire_start to compare
    # against, so any value invented for it would fence nothing while reading as
    # if it fenced something (the AUTOAPPLY F-5 dead-rail shape). The scheduled
    # rail's Phase 2.5 DOES pass it, because there the capture and the scoring
    # genuinely share one fire. Do not add a guessed timestamp here.
    # EVORDER layer 3 — when the message was SENT. A send cannot be evidence for
    # a promise captured after it. Pass it whenever the send carries a timestamp;
    # omitting it silently disables the guard.
    send_ts='<ISO-8601 ts of the send, or omit if the fetch carried none>',
    # F-28 — the workspace, so the roster reader can resolve a free-text
    # counterparty_name against entities.json + aliases. Without it, ONE person
    # a writer recorded as BOTH a resolved counterparty_id and that same
    # person's name counts as TWO: the close is downgraded to per-leg receipts
    # and the phantom name leg can never receive one, so the item stops being
    # closable through this path. `workspace_root` is already bound above.
    workspace_root=workspace_root,
)

# Stage B (F2): auto-resolves close through THE closure path — legacy-id
# normalization, loud no-match, full-set idempotency, pending_review floor.
# Matching (Path 1, thresholds) is UNCHANGED; only event construction moved.
n_resolved = 0
to_append = []
next_seq = <peek-next-seq from events.jsonl tail>  # for pending events only
for r in results:
    # v2.14.7+: HIGH-confidence → auto-resolve, MEDIUM → pending-review for
    # next Pulse fire to surface as a one-click confirm/skip item.
    if r['recommendation'] == 'auto_resolve':
        try:
            res = close_commitment(
                workspace_root, r['commitment_id'],
                resolved_by='<sender_person_id>',
                evidence=f\"Sent via Cowork — Subject: {<subject>}\",
                source_skill='apply-choices',
            )
            if res['status'] == 'closed':
                n_resolved += 1
        except (CommitmentIdError, PendingReviewError) as e:
            print(f'CRU skip {r[\"commitment_id\"]}: {type(e).__name__}', file=sys.stderr)
    elif r['recommendation'] == 'partial_received':
        # MC1: a send to ONE counterparty of a multi-counterparty commitment
        # records THAT person's receipt — never a whole close. Closure is
        # PROPOSED (never auto) once everyone is in.
        for cp in r.get('matched_counterparty_ids') or []:
            try:
                mark_partial_received(
                    workspace_root, r['commitment_id'],
                    received_by='<sender_person_id>', source_skill='apply-choices',
                    counterparty_id=cp,
                    evidence=f\"Sent via Cowork — Subject: {<subject>}\",
                )
            except (CommitmentIdError,) as e:
                print(f'CRU partial skip {r[\"commitment_id\"]}: {type(e).__name__}', file=sys.stderr)
    elif r['recommendation'] == 'pending_review':
        to_append.append(build_pending_review_event(
            commitment_id=r['commitment_id'],
            primary_thread_id=r['primary_thread_id'],
            source_skill='apply-choices',
            proposed_resolution='auto_resolve',
            score=r['score'],
            evidence=f\"Sent via Cowork — Subject: {<subject>}\",
            next_seq=next_seq,
            has_completion_signal=r.get('has_completion_signal'),
            evidence_ts='<this message send ts — the same value as send_ts>',
        ))
        next_seq += 1
if to_append:
    atomic_append_jsonl(events_path, to_append)
print(f'CRU: resolved={n_resolved} pending={len(to_append)}')
"
```

**The stdout is for diagnostic logging only; it MUST NOT appear in chat.** Per CONTRACT.md Rule 4 forbidden-pattern list: `commitment_resolved` and `commitment_review_proposed` event-type names never appear in user-facing chat. The user sees the resolution effect on the next Commitments fire (the resolved item simply doesn't appear).

If the CRU pass errors (helper import fails, events.jsonl missing, etc.): swallow silently. The send already succeeded; the CRU pass is best-effort enrichment, not a blocking gate. **Append a `pack_run.data.errors[]` entry** (v3.5.0+) to the most recent pack_run event in events.jsonl: `{"phase": "apply_choices_cru_pass1", "reason": "<short>", "detail": "<truncated stderr>", "ts": "<UTC ISO — never the local wall clock>"}` — the canonical errors[] sink `usage report` reads (origin in references/HISTORY.md § v3.5.0).

Three lines max for the overall ack. NO internal jargon. NO IDs. NO file paths. NO event-type names. NO "logged X event written to Y." NO "Pack run complete." Just human language.

Examples (good):
- *"Done — 4 of 5 applied. Rio added under Summit Company. Aspen logged for next Pulse. Item 4 needed a clearer date so it's still open."*
- *"Marked Aria items received (5 of 5). Nothing else outstanding from the Apr 8 call."*
- *"Pushed your 8:45 with Sam to next Saturday. Item 3 (Dustin Sample) skipped for 24 hours."*

Examples (forbidden):
- *"`person_063` added to `entities.json`, linked to `org_005`."* ← ID + file leaks
- *"`commitment_resolved` event written for #1 with `commitment_to_discuss` for #2."* ← event-type leaks
- *"Note: the Zapier-threaded send tool wasn't detected on this workspace, so the dispatcher fell through to native Gmail reply (preserved threading via thread_id). All three are continuations of the original threads, not new ones."* ← internal mechanics; on success this is forbidden trailing narration. Only surface the Zapier-not-detected note if a SEND ACTUALLY FAILED. On success, the path used is internal.
- *"Pack run complete. 4 dispatch events appended to `_hq/data/events.jsonl`."* ← internal narration + file path
- *"Per v2.12.0+ protocol, post-widget chat-links section emitted."* ← plugin-version leak

#### C. Mixed batch (some terminal, some draft-producing)

Plain-English ack line at top summarizing terminal outcomes. Widget below with the draft-producing outputs. Example:

> *"Pushed #4 to Saturday. #6 marked received. Drafts ready below for #1, #3."*
>
> [WIDGET: 2 items — Sam reschedule draft + Aria chase draft]

Same forbidden-pattern rules apply (no leaks, no version refs, no event-type names — see the forbidden examples in Step 4B).

**MANDATORY (v2.13.0+):** before posting the plain-English ack, run `validate_chat_output(ack_text)` from `chat_output_renderer.py`. It raises `LeakDetectedError` if any forbidden pattern matches. ABORT the post and rewrite the offending sentence. NEVER catch the error and post anyway. The contract is the contract.

### Step 5 — Append the audit-trail event (silent)

Single audit entry per Apply submission — ONE event, built by the canonical builder (T2.2, FS-18a — never hand-rolled):

```python
import sys; sys.path.insert(0, "shared/scripts")
from apply_audit import build_apply_choices_applied_event
event = build_apply_choices_applied_event(
    source="<resolved source id, e.g. cr-inbox / show-my-list / cr-brain>",
    actions=[{"n": "<n>", "action": "<verb as received>",
              "handler_result": <the handler's ACTUAL return dict, or the
                                  caught exception>}, ...])
```

**FS-18a — the outcome is the WRITE's outcome, never the dispatch's.** Every action's `handler_result` is REQUIRED and must be what the handler actually returned (its `status` field) or raised; the builder derives `outcome` ("ok" / "already_resolved" / "error") from it and refuses to be optimistic — an unknown status maps to "error", and a caller cannot pass an outcome directly. A refusal, a raised error, or a `needs_confirm` return is NEVER "ok" (the RV-5 false-success: a confirm whose handler wrote nothing was audited "ok", and the truth only surfaced in a correction pass 61s later). If a correction pass re-runs an action, the new audit row carries `correction_of` referencing the earlier event's seq.

Append via `atomic_append_jsonl(events_path, [event], holder="apply-choices")` from `shared/scripts/atomic_write.py` — OMIT `seq` AND `ts` (the gate auto-stamps both inside the writer lock, `ts` in UTC; a hand-typed "now" was the F-15 naive-local-clock bug class — v4.5.2 R4); never a hand-rolled append. The user does not see this; it goes to the events store. NO mention of it in chat output.

### Step 6 — Mixed batches

If the batch produces BOTH drafts/docs AND non-draft outcomes (e.g., 3 sends, 1 push, 2 skips), do BOTH:
- The widget surfaces the 3 drafts (per Step 4A)
- The plain-English line above the widget summarizes the non-draft outcomes ("Pushed #4 to Saturday. #5 and #6 skipped.")

The widget body is for things the user can still act on. The plain-English line above is for things already done.

## Forbidden behaviors (v2.12.4+ — REINFORCED)

- **No echoing the payload.** `apply choices: [{...}]` is wire format. Don't repeat it back.
- **No internal-jargon leaks** in the consolidated ack. Run `scan_for_id_leaks()` first.
- **No re-rendering the original widget.** apply-choices fires AFTER Apply; the source widget is done.
- **No partial replies.** Heavyweight outputs go in the widget OR the consolidated ack — not as separate chat turns.
- **No interaction-mid-batch.** Don't ask "should I continue?" partway through. The user already confirmed.
- **No silent dropping.** Every `{n, action}` tuple must succeed (in widget/ack) or fail (in errors line).
- **No retries.** If a handler errors, log and move on.
- **No version / protocol references.** "Per v2.12.4+ protocol..." is forbidden — that's developer-speak. The user doesn't know what version they're on.

## Why this exists

The all-batch button widget pattern (per `shared/CHAT_ACTION_WIDGET.md`) collects N selections in widget local state and submits them as ONE `sendPrompt` payload. apply-choices is the receiving end.

v2.12.4 extends the contract: Apply-time outputs that produce drafts or documents get the SAME widget treatment as the source orchestrator. Per M's Apr 30 ask: edit + send inline beats round-tripping through plain markdown.

## Compatibility / fallback

If the renderer is running in fallback typed-number-row mode (legacy), apply-choices is irrelevant — users type `N action` per line and the orchestrator's existing reply handler fires.

## See also

- `shared/CHAT_ACTION_WIDGET.md` — canonical widget spec.
- `shared/scripts/chat_output_renderer.py` — `render_chat_output_widget()` is the producer.
- Per-orchestrator "Reply handling" sections — the handlers this skill dispatches to.
- `_hq/CONVENTIONS_EMAIL_PREVIEW.md` — email draft format (used inside the post-Apply widget).
- `_hq/CONVENTIONS_SOURCE_LINKS.md` — source-link format (used in the post-Apply chat-links section).

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Receives the consolidated `apply choices: [...]` payload from the all-batch chat action widget's 'Apply all' button (and from the command-room-onboarding Step 1 v2 widget's Finish click). Parses the JSON array of {n, action, sub?, input?} tuples, dispatches each through the same handlers the source orchestrator emitted, and surfaces ONE consolidated chat ack. Apply-time outputs that produce email drafts or documents follow the SAME widget + clickable-link contract as the original orchestrator surface. Triggers ONLY on the exact prefix `apply choices: ` followed by a JSON array. Companion to `shared/CHAT_ACTION_WIDGET.md` (canonical widget spec) and `chat_output_renderer.py` `render_chat_output_widget()` (the renderer side; command-room-onboarding Step 1 v2 widget bypasses the renderer and is rendered raw via `mcp__visualize__show_widget`). Used by all 7 CR scheduled-task orchestrators (Pulse, Commitments, Inbox, Past Meetings, Upcoming Meetings, Morning Brief's mark-done route, Commitment Triage) plus on-demand `meeting-notes` Step 9 OPEN ITEMS and `show-my-list` surfaces plus first-install `command-room-onboarding` Step 1c.
