---
name: cleanup
surfaces: both
description: "Weekly self-maintenance. Tidies the workspace without the CEO's attention — runs Sunday night, auto-fixes what's safe, repairs damaged records, leaves a short Monday note only if something needs eyes. Triggers on 'weekly cleanup', 'clean up my workspace', 'clean up the workspace', 'tidy up', 'deep clean' (the full pass), 'roll over my session notes' (Step 0b) — and 'maintenance', 'run my maintenance', 'run maintenance now' (the background-maintenance manual fire — run what's due or report nothing due; Step 0, NOT a cleanup pass). (Bare 'clean up [a thing]' — an email, a doc, a list — is NOT this skill; only workspace-shaped cleanup fires it.) Also catches retired phrases 'weekly audit', 'system review', 'scan everything'. DOES NOT fire on 'weekly recap' / 'what happened this week' (that's weekly-recap), 'level up command room' (opt-in add-ons), or 'health check' / 'system health' / 'is everything running' (that's system-health — the scheduled-task watchdog)."
---

# Cleanup

One skill, one job: keep the customer's workspace tidy every week without requiring their attention. Runs weekly as a job inside the `maintenance` background task (MAINT1 — due at the Sunday 5:45 PM fire, and still due at the next fire if the computer was closed: `maintenance_dispatcher.due_jobs` self-heals missed weeks), fixes what it safely can, heals corruption, and leaves a short plain-English Monday-morning note for anything that needs the CEO's eyes. Replaces the retired `weekly-audit`.

The shift from the old audit: cleanup does NOT hand the user a score, a dashboard, or a "want me to fix these?" prompt. For a non-technical CEO the answer to "should I fix this?" is always yes, so cleanup just does the safe fixes and surfaces only genuine judgment calls.

## Write posture (the contract, stated once)

**Maintenance writes only reversible, receipted operations, and never asks permission for them.** "Read-only" is not this skill's contract and never was — unattended means *nobody is watching*, not *don't touch anything*. Read the two lists as written:

- **AUTOMATIC — no asking, ever:** archival moves into `_archive/`, memory rollover and compression (archive-never-delete), the bounded self-heal safe set, view regeneration, receipts, cursor advances. Every one is reversible and leaves a record of what it did. Deferring one of these to "say the word and I'll do it" is a contract violation, not caution — it is how ten session-notes files reached thirteen times their threshold while every weekly fire reported success.
- **ATTENDED — propose only, unchanged:** preference and skill-config writes (FRP1), capture drops (a wrong close is silent, so it stays a manual click), and any repair the checks cannot call safe. These are flagged, never mutated.

When a step below says AUTOMATIC, run it. The safety copy, the archive, and the receipt are what make it safe to run unwatched — asking first adds nothing but a stalled backlog.

## Skill Boundary (v2.1)

- **Use cleanup for:** the weekly (or on-demand) workspace maintenance pass — safe auto-fixes, substrate self-heal, and the short Monday note. Retired audit phrases ("weekly audit", "system review", "scan everything") redirect here.
- **Use `weekly-recap` for:** "weekly recap" / "what happened this week" — the week-in-review narrative, not maintenance.
- **Use `system-health` for:** "health check" / "system health" / "is everything running" — the scheduled-task watchdog (moved out of cleanup in Phase 3/W1).
- **Use `level-up-command-room` for:** "level up command room" — it answers the one dashboards sentence (dashboards live in chat now; nothing installs into a sidebar).
- **Does NOT fire on** bare "clean up [a thing]" (an email, a doc, a list) — only workspace-shaped cleanup fires it.
- **Maintenance-shaped phrases run Step 0 below, NOT a cleanup pass** — "run my maintenance", "run maintenance now", "run maintenance", bare "maintenance" (EW2+T, F-14).

## Step 0 — Manual maintenance dispatch (EW2+T, F-14; runs INSTEAD of the phases below)

Post-MAINT1, the bridge and the install ritual teach the customer they have a "Maintenance" background task — so the natural phrases "run my maintenance" / "run maintenance now" / bare "maintenance" mean **fire that task's due-jobs engine now**, not "run a full workspace cleanup" (the live D8 fire ran a duplicate cleanup pass instead; harmless but not what was asked). When the firing phrase is maintenance-shaped:

**Run each job by its OWN leg, not by its name (FIX3 F3-6, ruling R-RW-5).** Every row in `plan["jobs"]` carries `leg` — the whole command, already ending `--fired-via manual --triggered-by <this surface>` — and `env`, the same two answers as variables. On a legacy or local seat, paste `job["leg"]` verbatim; where the job is a SKILL rather than a script `leg` is empty, and then export `job["env"]` before running it. On a merged seat the plan line is what crosses the door and the layer forwards the same two variables. This is not decoration: on 2026-09-21 four upkeep jobs ran inside a hand-typed morning brief and all four recorded themselves as a scheduled fire, because "execute each job's skill end to end" gives a flag nowhere to go.

**This engine now has a second caller (HEAL1).** `morning briefing`, `end of day` and `weekly recap` ask the same dispatcher the same question before they gather, run what it returns, and receipt it once with `triggered_by` naming the surface — see `maintenance_dispatcher.catch_up_plan`. Nothing below changes: this step is still the door someone walks through when they type the words, and it still runs the WHOLE registry. Two things follow from having two callers, and both are already in code rather than in this paragraph: a catch-up minutes ago means this fire finds nothing due (the receipt is the same receipt), and the Sunday family runs from here and from `weekly recap` and from nowhere else (R-M2).

1. Resolve the workspace + plugin root per CONTRACT Rule 22 (multiple plugins may be installed — filter the plugin_* candidates by this plugin's name, the MAINT-RUN discovery wobble).
2. Ask the dispatcher what is due — NEVER judge due-ness yourself: `python3 shared/scripts/maintenance_dispatcher.py <workspace_root>` and hold its JSON plan (`due` is ordered; the order is the contract). Where the files are on another machine the same question goes through the access layer — `maintenance_dispatcher:catch_up_plan` with `"surface": "run-maintenance"`, one verb, one call, rendered by `workspace_access.py plan run_helper` per the Access preamble. That surface is exempt from the two gates the typed surfaces get: somebody asked, so it plans the whole registry whether or not the last slot was served. In the cloud container the plan comes back with every job that carries an `--apply` leg held out under `refused_container` — the APPLY class (`maintenance_dispatcher.apply_class_jobs`), which is NOT every job that writes: a job whose only write is its own receipt still runs there. Those held jobs stay owed, and `refused_line` is the one sentence to say about it here — plainly, once, with no count and no part named. Nothing here passes a host mode: this step is the door a person types their way through, and the resolution `surface_drivers.resolved_host_mode` does belongs to the typed read surfaces that call `maintenance_catch_up`, not to this one.
3. **Nothing due →** the composer's nothing-due line (step 5 below builds it from the same plan) and STOP. Write NO `maintenance_run` receipt for a nothing-due manual poke: the watchdog reads `maintenance_run` for task freshness, and an empty manual receipt could mask a broken scheduled task.
4. **Jobs due →** execute each due job's skill END-TO-END in plan order, one at a time, never in parallel — identical rules to the registered task prompt: score each job through `maintenance_dispatcher.job_counts_as_complete(job_id, receipt_validated=…, run_reported_nothing_due=…)` — ordinarily a job is COMPLETED only when its OWN receipt validator confirms its substrate receipt, and an unreceipted job goes in jobs_failed and stays due (self-healing); the one exception the function knows is a job in `maintenance_dispatcher.QUIET_RUN_JOBS` whose own return said there was nothing due, because those write no receipt on a quiet run by design. Never widen that yourself: reporting a correct nothing-to-do run as a failure is what put seven phantom question-expiry failures on the maintenance report. Then finish with ONE `maintenance_dispatcher.maintenance_receipt(workspace_root, jobs_due=…, jobs_completed=…, jobs_failed=…, skipped_disabled=…, fired_via="manual")` and confirm it landed via `validate_maintenance_ran`.
5. **Chat output comes from the composer, not from the plan (SPEC FIXTRAIN 6.2 — MANDATORY).** On 09-13 this fire answered with an "Internal record" paragraph: a raw record number, a command-line flag, the job ids verbatim and a field name with UNKNOWN after it. All four were the plan dictionary being read out loud. **Never print that dictionary, in whole or in part, in any shape** — it is a machine's report to another machine. Build the answer from it instead, and print what comes back:

```python
import sys
sys.path.insert(0, "shared/scripts")
from surface_composers import maintenance_answer
print(maintenance_answer(plan, completed=jobs_completed, failed=jobs_failed,
                         findings=findings, workspace=workspace_root))
```

`findings` is a list of already-plain sentences about the CEO's own items ("Two promises to Acme have gone quiet for six weeks.") — each job's own must-surface lines per its SKILL.md go here. The composer covers all four cases (nothing due, a quiet run, findings, a job that did not finish), says every job in words rather than by id, and refuses rather than returning if a finding you handed it carries an internal name. There is no "Internal record" section, no receipts narration, and no event-type name anywhere in this answer.

**The whole reply goes through one door (SPEC FIXTRAIN v5.31.0 6.1, R-25 — MANDATORY).** A composer gates the sentence it built; it cannot gate the sentences typed after it. Eleven of the thirteen leaks on the v5.31.0 record were exactly that shape — a clean composed answer, then an ungated paragraph naming files, functions, event names and writer ids. The Run Now answer was clean on 09-15 and the Staff Meeting's plumbing narration ("emit run", "seven-day dedupe") was not — same class, one turn later. So compose everything you intend to post, hand it to `post` ONCE, and print what it returns as your entire reply.

```python
import sys
sys.path.insert(0, "shared/scripts")
from surface_composers import post
# `composed_text` is `maintenance_answer`'s return, in this same run -
# relay it, never retype it. The door vouches a relay LINE FOR LINE
# against what a composer here actually returned, so a sentence you
# wrote yourself cannot ride in as one. Nothing here is the CEO's own
# typed text, so no rows are named; where a section IS composed around
# their words it passes `customer_rows` and the door reads the words
# off those rows itself (see the undo receipt in workspace-manager).
print(post(whole_reply, surface="maintenance", workspace=workspace_root,
           relayed=composed_text))
```

`relayed` is the composer's own return, and the door checks it twice: for PRESENCE, IN ITS OWN ORDER (paraphrasing it instead of relaying it is a refusal, not a style — and so is shuffling its lines or repeating one of them: a relay is the composer's return, not its ingredients) and for ORIGIN — every line of it must be a line a composer returned in THIS same run, and the check is in full: one unvouched line refuses the whole post. There is no share of a turn a caller may claim as already-checked. `customer_rows` is how a reply says it was composed around the CEO's own words: you name the ROWS (by seq, or by row id) and **the door reads their customer-typed fields off the book itself**. There is no argument for the words — you cannot tell this door what they typed, only which of their rows to go and read, and a call with no `workspace` declares nothing at all. **Be exact about what a declaration does**: it blanks the declared fragment out of the copy the INTERNAL-NAME classes read — `_hq/` paths, data-file and module names, script names, build codes, the vocabulary roster and the record counter — which is most of this gate, so it is not something to hand yourself. Record ids and the absolute-path scan read the whole text whatever was declared. Everything undeclared is this product's own words and is scanned in full. `post` raises rather than returning, and nothing is caught. **If it refuses, post the composer's return on its own** — it is already gated, and the paragraph that could not pass is the paragraph that should not have been written; **if even that refuses, post `surface_composers.refused_line(<surface>)` and nothing else** — one honest sentence that it could not put the answer together, with the phrase offered again. **There is no sentence after it.**

## Step 0b — "roll over my session notes" (the ATTENDED notes pass; runs INSTEAD of the phases below)

This is the remedy the Monday note points at when Phase 2 item 4 returned `aborted` records — never a precondition for the automatic pass, which needs no permission and never waits to be asked. Run it when the phrase is notes-rollover-shaped ("roll over my session notes", "roll over my notes"):

1. Run `cleanup_actions.rollover_session_notes(workspace_root)` — the SAME function, nothing hand-rolled. Anything it can do safely, it does now.
2. Report the `rolled_over` count in one plain line.
3. For each `aborted` record, say in plain English what shape is in the way — *"[Project]'s notes keep their entries one level down under a 'Session Log' heading, so I can't tell where one session ends and the next begins"* — and ask whether to normalise those headings together. One file at a time, never a bulk offer.
4. **You still never hand-reshape the file.** If the CEO says yes, the only edits you make are to the HEADINGS the record named (promoting `###` session entries to `##`, dating an undated `## Meeting:` block, putting an out-of-order entry back in sequence). Then re-run the function and let it do the split, so the safety copy, the conservation check and the receipt apply exactly as they would on a Sunday night.
5. If the CEO declines, leave the file untouched and say so. A notes file nobody wants restructured is not a problem to keep raising.

Cleanup-shaped phrases ("weekly cleanup", "clean up my workspace", "clean up the workspace", "tidy up", "deep clean", "scan everything", "weekly audit", "system review") run the full pass below, exactly as before. When cleanup runs AS a job inside a maintenance fire (scheduled or Step-0 manual), it starts at Phase 1 directly — Step 0 is the entry router for the chat phrase only.

## Personification Contract (v3.13.8.4+)

Before surfacing the summary or composing the `.docx` report, read `shared/PERSONIFICATION.md` and call `shared/scripts/personification.py::get_brain_name(workspace_root)`. The chat summary intro uses the shape `"Cleanup done, {first_name} — {brain_name} tidied up {N} things this weekend."` The `.docx` report (when generated) opens with the same author line in the header. Default `{brain_name}` = `"Penelope"`.

## Writer Contract

- **Primary writer for** `_hq/cleanup-reports/[YYYY-MM-DD]-cleanup.docx` (via `shared/scripts/brief_writer.py` per CONTRACT Rule 27 — no .md deliverables).
- **Appender** for `cleanup_run` events to `_hq/data/events.jsonl` and for `_hq/CONFLICTS.md`.
- **Auto-fix writer** for the safe maintenance actions in Phase 2 and the safe integrity remediations in Phase 3 (see those phases for the exact, bounded write set).
- **Brain Live State renderer (v3.17.0+)** — re-renders each active project's `PROJECT_BRAIN` Live State block via the canonical `render_thread_live_state` / `render_brain_block` helpers (marked-region write, byte-preserves hand-written content) and runs the idempotent one-time brain migration (Phase 3.5). Never hand-edits a brain; only the helper touches the marked block.
- **Derived-view + scaffold + lock writer (bounded, v3.19.x / SPEC CLEAN1)** — regenerates the views it owns from the substrate (`_hq/views/PEOPLE.md` via Phase 3.5c; `_hq/views/DECISION_LOG.md` via the **changed-only** `render_decision_log.regenerate_if_changed`, Phase 3.5d), scaffolds a **missing** `SESSION_NOTES_[NAME].md` (Phase 3c / D3 — never overwrites one that exists), and **archives** `*.lock.stale.*` sentinels older than 1 hour into `_archive/stale-locks/` (Phase 2 Rule 9 / D6 — moved, never deleted). Every one of these is idempotent: re-running on a clean workspace writes nothing.
- **Living Brain expiry sweep (SPEC LB1, Phase 3j)** — appends the silent `brain_proposal_expired` tombstones for open proposals past their TTL, ONLY via `brain_proposals.expire_stale()` (the canonical sweep helper — never a hand-rolled append). Idempotent: nothing stale → nothing written.
- **Config-drift proposals (SPEC LB2, Phase 3k — bounded rider, the `expire_stale` precedent)** — the weekly drift pass may append `brain_proposal` rows (`kind: config_drift`), ONLY via `config_drift_detector.run_drift_detector()`. **This is not a pref write: cleanup stays READ-ONLY on `_hq/data/skill_config/` — byte-checked by test.** The proposal re-offers a knob; only the user's tune flow ever writes config.
- **Objective-link proposals (SPEC OBJ2 §1B, Phase 3l — same bounded-rider shape as 3k)** — the weekly link pass may append `brain_proposal` rows (`kind: objective_link`), ONLY via `objective_link_detector.run_objective_link_detector()`. The detector consumes the classification envelope's existing stamps through the org-scoped event seam — it never hooks a capture pipeline, re-reads content, or writes anything but the proposal; adjudication (confirm/dismiss on the Staff Meeting card) is the only thing that ever binds a link.
- **Readalarm sidecar pruning (SPEC LB2 D5 — the ONE delete exception)** — deletes `*.readalarm.json` sidecars whose recorded failure is >30 days old, ONLY via `cleanup_actions.prune_stale_readalarms()`. Derived alarm state, never substrate or user files; a sidecar younger than 6 days (2× the surfaced window) is never touched regardless of settings. Everything else stays archive-only.
- **NEVER writes:** `entities.json` (except via the owner skills' helpers it delegates to), `events.jsonl` other than appending one `cleanup_run` event (+ the `corruption_recovery` event that `recover_corruption.py` appends on its own, + the Phase 3j expiry tombstones via `brain_proposals.expire_stale`), `aliases.json`, the **analytical views** (`RELATIONSHIPS`/`TIMELINE`/`COMMITMENT_AGING`/`DORMANT`/`THEMES` — insight-generator owns those; cleanup only flags them stale) or `_hq/views/ALIASES.md` (people-crm owns it — flag only), and `classifier_feedback.jsonl`. Never deletes a user's folders or files; orphan folders are FLAGGED, not removed. Canonical entity mutation stays with the owner skills (workspace-manager / project-manager / people-crm).

## Substrate validated every run

Every run validates the v2.2 data substrate per `references/DATA_CONTRACT.md` and the JSON Schemas in `shared/data-schemas/`. The deterministic checker (Phase 3) is the executable backstop; the prose contract in DATA_CONTRACT.md remains the spec.

---

## Phase 1: Silent Scan (no questions)

Scan the entire workspace before doing anything. Resolve `[WORKSPACE_ROOT]` via the canonical CONTRACT.md Rule 22 discovery preamble (find `_hq/` under the mount). Projects live at `[WORKSPACE_ROOT]/[Project Name]/` (root level); infrastructure folders carry a `_` prefix.

### 1.0 — Deterministic structural scan (CODE, runs first, EVERY fire)

The structural folder↔thread reconciliation runs as **code, not prose** (SPEC CLEAN1 / D1) — five real weekly runs reported "clean" while six hygiene classes accumulated because the scan below used to be prose the model skipped under load. This block executes on **every** cleanup fire (weekly included — NOT deep-clean-only, D2). Capture its findings and carry them into Phase 3 remediation + the Phase 4 Monday note.

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import integrity_check as ic
ws = '<workspace_root>'
findings = ic.scan_project_structure(ws)
out = {'orphan_folder': [], 'missing_brain': [], 'missing_session_notes': []}
for f in findings:
    if f.check == 'C10.orphan_folder': out['orphan_folder'].append(f.subject)
    elif f.check == 'C11.missing_brain': out['missing_brain'].append(f.subject)
    elif f.check == 'C11b.missing_session_notes': out['missing_session_notes'].append(f.subject)
print(json.dumps(out))
"
```

- `orphan_folder` → **FLAG only** in the Monday note (never delete — see Phase 3d). On a live client workspace these are almost always hand-made folders the CEO created on purpose.
- `missing_brain` → feeds Phase 3c brain backfill (Rule 15).
- `missing_session_notes` → feeds Phase 3c session-notes backfill (Rule 16, D3).

The prose checks 1a–1j below remain the broader, judgment-driven sweep layered on top of this deterministic core — they are NOT a substitute for it.

- **1a. Project health** — for each project folder: PROJECT_CONTEXT.md present + last-modified; SESSION_NOTES_[NAME].md present + most-recent entry; loose files; structure complete. Cross-reference stage in MASTER_TRACKER against file activity.
- **1b. Master tracker integrity** — every tracked project has a folder (or exploring notes); every folder has a tracker entry; phantom entries (active but folder missing); orphan folders (folder, no entry); plausible "Last Touched" dates; overdue commitments; aging Inbox items (30+ days).
- **1c. HQ infrastructure** — BUSINESS_CONTEXT.md / MASTER_TRACKER.md currency; temp/.old/stale artifacts.
- **1d. Session-notes freshness** — most recent entry per active project; flag active projects past their staleness threshold.
- **1e. Skills health** — outdated path/config references; descriptions accurate; long-unused skills.
- **1f. Intel system (optional)** — only if `_hq/intel/` exists; else skip entirely.
- **1g. File size & bloat** — against WORKSPACE_SCHEMA.md targets: SESSION_NOTES >150 lines, PROJECT_BRAIN >4KB, PERSON files >3KB, MASTER_TRACKER >2KB, `_hq/briefings/` >30, `_people/prep/` >20, `_hq/cleanup-reports/` >12, any .md >10KB.
- **1h. Team health (if `_people/` exists)** — else skip entirely. Last interaction, open/overdue commitments per member; flag 14+ days silent, 3+ overdue, profiles >30 days stale.
- **1i. Content accuracy** — cross-reference docs vs recent session notes to find drift (stale contexts, dormant "active" projects, undocumented people/decisions).
- **1j. Prospects that look converted (Bug #92 — detect-and-nudge, NEVER auto-flip).** Run `shared/scripts/prospect_conversion_detector.py::detect_prospect_conversion_candidates(workspace_root)`. DEALNAG1 + M's ruling 4 (2026-09-03): a paid or signed fact (a won deal thread, a `deal_won` / invoice / payment / agreement event) PROMOTES the org automatically — `org_promotion` does the flip with a receipt and an undo, so it never reaches this note. What the detector still returns is the ambiguous lane: signing language, and a settled org that could not be promoted because no primary-focus org is set. A sizing or engagement record alone never qualifies for either. Render what the detector returns, never re-derive a candidate from the entities file. For any candidate, add a line to the Monday note's "worth a glance" tier — *"[Name] looks like a client now ([reason]) — say `[Name] is now a client` to convert."* This is the weekly backstop for the real-time coach nudge. Cleanup does NOT change `relationship_type` itself — it only surfaces the suggestion; the CEO runs the Bug #91 conversion.
- **1k. Commitment write-contract violations (Phase 2 Stage D, S4 — flag-only).** `integrity_check.run_checks` now emits `C17.cleanup_keys` (any event carrying `_cleanup_*` keys — the signature of a hand-rolled in-place edit) and `C17.inplace_status` (commitment events with a closed-family `data.status`; legacy rows read fine forever, but GROWTH week-over-week means an active F4 mutation writer). Surface both in the Monday note as contract violations in plain English (*"something edited your activity log the unsafe way this week — nothing lost, but worth flagging"*). Cleanup NEVER rewrites the rows itself — F4 applies to cleanup too; commitment closure is `close_commitment()` appends only.

## Phase 2: Auto-Fix Sweep (does it, doesn't ask)

On the FIRST cleanup run per workspace only (track via `entities.json` `workspace.cleanup_intro_shown: true`), print this one-paragraph reassurance so the user understands what's about to happen. Subsequent runs skip it and just do the work:

```
Running cleanup. Quick note on what this does: I never delete anything.
Old **caches** — briefings, prep briefs, reports — get moved into an
`_archive` folder so your working space stays tidy, but they're still there
if you ever want them. Your **memory** — session notes, decisions,
commitments, interaction logs — is compressed when it gets old but kept
forever. If you ever ask "what has [person] delivered this year," the answer
is still there.
```

Then run all automatic maintenance rules from `workspace-manager/references/maintenance-rules.md` WITHOUT asking:

1. **Briefing archival** (Rule 4): briefings older than 30 days → move to `_archive/briefings/` — CACHE (archived, never deleted)
2. **Report archival** (Rule 5): keep only 12 most recent cleanup reports → move older ones to `_archive/cleanup-reports/` — CACHE (archived, never deleted)
3. **Prep file archival** (Rule 6): prep files older than 14 days → move to `_archive/people-prep/` — CACHE (archived, never deleted)
4. **Session-notes rollover** (Rule 1 + Rule 12): SESSION_NOTES over 150 lines → keep the live sections and the 5 newest entries, archive the older full entries to `SESSION_NOTES_[NAME]_archive_[YYYY].md`, add the Session-History lines and the index rows — MEMORY (archived, never deleted). **This is AUTOMATIC and is never deferred to an attended ask.** The pass never waits to be asked — there is no "say 'roll over my session notes' first" gate and never was: the pre-reshape copy under `_archive/session-notes-pre-rollover/` IS the permission, and it is written before the first byte of any reshape. (That phrase is the remedy for the files this pass REFUSES — the `aborted` records — never a precondition for the ones it can do itself.) Run the code block below; fold every returned record into `actions_taken[]`.

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
records = ca.rollover_session_notes('<workspace_root>')  # >150 lines only; archive-only, safety-copied, idempotent
print(json.dumps(records, indent=2))
"
```

> **Read the records, don't re-derive them.** `rolled_over` = the file was reshaped (the record names the archive files, the index, and the safety copy). `skipped` = over threshold but nothing old enough to move — normal, no note. `aborted` = the file's structure was one this rollover will not guess at (entries nested under H3, an undated section between dated ones, an out-of-order append, or the conservation check finding a line that would be lost); the file is byte-identical and NOTHING was written for it. Never hand-reshape an aborted file to "finish the job" — the abort is the finding. Aborts get exactly ONE counted line in the Monday note, decided from THIS run's records alone (see the Beat 1 guidance below) — the fire has no memory of previous weeks and must never pretend to.


5. **Brain-thread pruning** (Rule 2): compress resolved threads >30 days to a Thread-History one-liner — MEMORY (compressed)
6. **Commitment archival** (Rule 3): compress delivered commitments >60 days to a Commitment-History one-liner — MEMORY (compressed)
7. **Tracker hygiene** (Rule 8): move old Recently Archived entries into the tracker's own archive section — STALE POINTERS (archived in place, never deleted; project archive folders remain). CTS1: the markdown quick-task lane is retired — if a legacy "Quick Tasks" section with LIVE rows survives, run workspace-manager's one-time quick-task migration (rows → `kind: task` commitment events) instead of grooming the section; "Completed Quick Tasks" rows file under `## Archived (history)`.
8. **Interaction-log tiered compression** (Rule 7): Tier 1 (0–90d) full, Tier 2 (90d–6mo) one-liners, Tier 3 (6mo–1yr) monthly digests, Tier 4 (1yr+) archived — MEMORY (compressed)

> **The three compression rules above (2, 3, 7) are AUTOMATIC and never deferred to an attended ask** — same contract as the rollover, for the same reason: they are archive-never-delete memory operations, so there is nothing to ask permission for. They still compress by judgment rather than by a helper, so the receipt is the only proof they ran: **every one of them that does anything must land a record in `actions_taken[]`**, and a week where all three report nothing is a claim that all three ran and found nothing to move. If receipts show they are silently skipping the way the rollover was, they get scripted next — that is a separate spec, not a fire-time improvisation.

9. **Stale lock-file archival** (D6): move `_hq/data/*.lock.stale.*` and `_hq/.system/*.lock.stale.*` sentinels older than **1 hour** into `_archive/stale-locks/` — STALE LOCKS (not memory, not caches; moved, never deleted). Run the code block below; record the count into `actions_taken[]`. A 1-hour floor means a writer that's still mid-recovery is never disturbed; the docstring on `atomic_write.py` long claimed "weekly Tidy Up cleans them" but nothing did — this is the implementation.

```bash
python3 -c "
import sys; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
archived = ca.sweep_stale_locks('<workspace_root>')  # >1h-old only; archive-move, never touches fresh locks
print('archived', len(archived), 'stale lock files')
"
```

> **A1 coordination (reconciled — A1 shipped second):** SPEC A1's stale-lock sweep requirement is already satisfied by `cleanup_actions.sweep_stale_locks` above (covers `_hq/data/` + `_hq/.system/`, >1h floor) — A1 added **no** duplicate sweep. A1 adds ONLY the contention-reporting step below. Exactly one owner of the stale-lock sweep: this Rule 9.

10. **events-lock contention report** (A1): the events.jsonl writer lock records best-effort contention counters in `_hq/.system/lock_stats.json` (only when a wait exceeded 100ms — a quiet workspace has no file). Read it, fold a plain-English line into the Monday note (Beat 1), then **reset** it so each week's report covers just that week. This is read-report-reset, NOT a sweep. Run the code block below; record nothing into `actions_taken[]` (reporting only).

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from pathlib import Path
from atomic_write import atomic_write_json
p = Path('_hq/.system/lock_stats.json')
if p.exists():
    try: s = json.loads(p.read_text(encoding='utf-8'))
    except Exception: s = {}
    waits = s.get('waits', 0); timeouts = s.get('timeouts', 0)
    fb = s.get('fallback_sentinel_acquires', 0)
    print(f'events lock contention this week: {waits} waits, {timeouts} timeouts, {fb} sentinel fallbacks')
    atomic_write_json(p, {})  # reset for next week
else:
    print('events lock contention this week: none (no waits over 100ms)')
"
```

11. **Stale readalarm-sidecar pruning** (LB2 D5): delete `*.readalarm.json` sidecars under `_hq/` whose last recorded read-failure is older than 30 days — the evidence has been surfaceable by dozens of brief/system-health fires already, and the sidecars otherwise accumulate forever next to substrate files. Run the code block below; record the count into `actions_taken[]`. This is the workspace's ONE true delete (M ruling 2026-07-19): sidecars are derived alarm state — machine telemetry, never substrate, never a user's file. The helper enforces a hard floor (never touches a sidecar younger than 2× the surfaced window) no matter what it's called with.

```bash
python3 -c "
import sys; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
pruned = ca.prune_stale_readalarms('<workspace_root>')  # >30d-old sidecars only; the one delete exception (D5)
print('pruned', len(pruned), 'stale read-alarm sidecars')
"
```

11a. **What a refused delete does, and the ONE delete ask per session** (DEL1, gap analysis row 9 + §0.26). On a merged seat this workspace is reached from a sandbox, and a delete there is REFUSED until the customer grants it — `PermissionError`, every time, not now and then. Two halves, and only the second one involves the customer:

  - **The fallback is automatic and asks nothing.** Every runtime delete in Command Room goes through `shared/scripts/delete_grant.py::remove_or_move_aside`: it tries the delete, and when the mount refuses it RENAMES the file aside (`.stale.<epoch>.<pid>`, or `.archived.<epoch>` where the content already reached its new home), says one line naming the file, and never raises. The operation around it finishes — the profile is restored, the notes are written, the reconfigure applies, the rollback completes — and no live reader still sees the file. Nothing in this rule, and nothing anywhere else in this skill, needs permission for any of that.
  - **The ask happens at most ONCE per session, only from this skill, and only on a seat that HAS the tool.** `device_request_delete_permission` is a device-bridge tool: it exists on a merged seat and NOWHERE else, so on a legacy Cowork seat or a Claude Code seat there is nothing to call and nothing is asked. It puts a consent card in front of the customer, so it is rationed in code, not by good intentions: `delete_grant.may_ask()` is the read — true at most once per session, on the merged seat only — and `delete_grant.mark_asked(outcome)` is the write, run AFTER the tool comes back, so a seat with no such tool can never spend the session's one ask on a call that did not happen. **No other skill may name or call that tool** — `tests/run_del1_test.py` fails the build on a second home for it.

Run the block below. It reports what is past its keep and answers whether an ask is allowed; it spends nothing:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca, delete_grant as dg
report = ca.runtime_cache_report('<workspace_root>')
past_keep = len(report['retire'])
gate = dg.ask_gate() if past_keep else {'may_ask': False, 'reason': 'nothing_past_keep'}
print(json.dumps({'past_keep': past_keep, 'may_ask': gate['may_ask'],
                  'why': gate['reason'], 'seat_has_tool': dg.ask_is_available(),
                  'state': dg.grant_state()}))
"
```

  - `may_ask` **false** → do not ask, this run or any later one in this session, and do not name the tool. Nothing is deleted; every delete is the move-aside above. Put `why` into `actions_taken[]` and nowhere else: `seat_has_no_tool` is the ordinary answer on a legacy or local seat, `no_session_identity` means this seat has the tool but nothing in it names the session — the two blocks here are two separate programs, so a ration nothing can carry between them is not spent at all (fix round 2, finding M-5) — and `already_asked_this_session` is the ration doing its job. **None of the three is a fault and none of them is ever said to the customer.**
  - `may_ask` **true** → call `device_request_delete_permission` ONCE, for the workspace root, with a one-sentence plain-English reason ("old copies of my own working files are piling up and I can only clear them with your say-so"). Then record what came back — granted, declined, or no answer at all — so the Monday note can say it and the ration knows the answer:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import delete_grant as dg
print(json.dumps(dg.mark_asked('<granted|declined|not_asked>')))
"
```

A **decline is a full answer, not a retry cue**: nothing is deleted for the rest of the run, nothing is asked again, and no line anywhere treats it as a problem — the move-aside was always the fallback and it works. Record the outcome (granted / declined / not asked) into `actions_taken[]` every week; the Monday note gets a line only when the customer actually answered (Beat 1 below, `delete_grant.monday_note_line()`). Never surface a path, a version number, or the tool's name to the customer.

**The asides are counted, never collected** (fix round 1, M-2). On a delete-blocked mount every delete in Command Room becomes a rename, permanently, so the set-aside files accumulate under the system folder. This pass COUNTS them and says so once; it moves nothing and removes nothing — a sweep of them is a separate decision nobody has made. Run the block below and fold the line into the Monday note (Beat 1). Zero adds nothing.

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import delete_grant as dg
report = dg.report_asides('<workspace_root>')
print(json.dumps({'report': report, 'line': dg.asides_note_line(report)}))
"
```

12. **CLAUDE.md edit guard** (CLAUDEMD1 Defect B): ANY pass in this skill that rewrites or compresses `CLAUDE.md` — including "tidy the quick-reference file" style compression — must snapshot the file text BEFORE editing and run `shared/scripts/claude_md_guard.py::report(before, after)` after. When `ok` is false, surface **every line in `removed` verbatim** in the session output and the Monday note — by content, never as a count ("removed 3 lines" is the bug, not the report). For each line in `removed_rules` (imperative operating instructions — the draft-posture class that was silently deleted 2026-07-29): restore it verbatim, or refuse the compression for that section and say why. Compression may reword; it may not drop. A backup on disk is recovery, not disclosure — writing one does not satisfy this rule. Generated `LIVE-STATE` blocks are machine-owned (render_claude_md redraws them) and are outside this guard by construction; regenerate them via `shared/scripts/render_claude_md.py` instead of editing them.

13. **Dangling review-proposal drain** (REFINT1): review proposals whose commitment was never created are reachable by NO surface — the review tier and amnesty derive from `commitment` events, so a dangling row appears in no count and no list while "nothing has sat unanswered" reads literally true. The write gate refuses new ones since REFINT1; this drains the residue: each orphaned question is terminally closed with a `commitment_review_dismissed` carrying `resolution_reason: "target_never_created"` (an appended tombstone — nothing deleted, the proposal rows stay in history, and calibration readers know it is a system lapse, not your "not relevant"). The drain REFUSES to apply when the log has unparseable lines (`refused: "unparseable_lines"` in its result) — surface that line in the Monday note and let the 3a/3b heal pass fix the corruption first; the drain catches up next week. Run the code block below; fold each returned line into `actions_taken[]` AND into the Monday note (Beat 1) — the note is the "surfaced once for a human" half, so the lines go in verbatim, by content, never as a bare count.

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from commitment_backlog_sweep import dangling_review_drain
out = dangling_review_drain('<workspace_root>', apply=True)
print(json.dumps({'n': out['n'], 'lines': out['lines']}, indent=2))
"
```

14. **Unconfirmed swept session-notes disclosure** (SWEEPSTALE1 — the SESSQUAR1 follow-up): the nightly sweep quarantines machine-composed session-notes blocks in a `.swept.md` sidecar next to each project's notes file; the "end session" ritual is what folds them in. A project that is only ever swept and never end-sessioned accumulates an unpromoted sidecar forever, silently — this item is the weekly disclosure. Run the code block below (read-only — it counts, it never promotes, creates, or removes anything; promotion stays the CEO's confirming touch). If the list is non-empty, add ONE plain-English line per project to the Monday note's "worth a glance" tier, oldest-first, capped at 3 projects with an "…and N more" tail: *"[Project] has N session-note entries I drafted overnight that you haven't confirmed yet (oldest [date]) — say 'end session' next time we work on [Project] to fold them in."* An empty list adds nothing — no line, no "all confirmed" filler (COVERQUIET1 posture). Record nothing into `actions_taken[]` (reporting only).

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import session_narrative as sn
pending = sn.pending_swept('<workspace_root>')  # [] on a fully-confirmed workspace
print(json.dumps(pending, indent=2))
"
```

These rules are non-destructive by design — **nothing is ever deleted, with the ONE ruled exception of Rule 11's stale read-alarm sidecars (derived machine telemetry, LB2 D5).** Memory is compressed/archived in place; caches and >1h-stale locks are MOVED into `_archive/` (never a user's folders or files). Record each action taken into `actions_taken[]` for the `cleanup_run` event.

## Phase 3: Substrate Integrity (detect → remediate)

This is the self-healing core. Two steps: a read-only **inspector** finds problems, then cleanup **remediates** the safe ones.

### 3a. Run the inspector (read-only DETECT)

```
python3 shared/scripts/integrity_check.py <workspace_root> --json
```

Returns structured findings with severity ERROR / WARN / INFO across ~13 referential checks: missing/malformed ids, unresolved affiliations, org/thread parent cycles, engagement endpoints, person↔org/thread link symmetry, dangling event references (test-residue detector), dead aliases, orphan folders, thread `folder_name` missing on disk, missing PROJECT_BRAIN, and **duplicate event seq**. The checker NEVER fixes — it only reports. Fold its findings in; do not re-derive them by hand.

**Snapshot the findings here — Phase 3z reconciles against them (FOLDERGUARD §2.5).** Keep the Phase 3a result in memory for the rest of the fire:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import integrity_check as ic, phase_order_guard as pog
from pathlib import Path
snap = pog.snapshot(ic.run_checks(Path('<workspace_root>')))
Path('<session_dir>/phase3a_snapshot.json').write_text(json.dumps(snap), encoding='utf-8')
print('phase 3a snapshot:', len(snap), 'findings')
"
```

### 3a-bis. Lint skill settings (read-only DETECT — settings-layer C4)

```
python3 -c "import sys,json; sys.path.insert(0,'shared/scripts'); \
from skill_config_writer import lint_skill_configs; \
print(json.dumps(lint_skill_configs('<workspace_root>')))"
```

Returns `{skill: [dangling keys]}` for any skill whose saved settings carry a key that
is no longer in `shared/data-schemas/skill_config.schema.json` (a deprecated key left
behind after a knob rename, or drift). Read-only — cleanup never edits a saved setting
(FRP1 precedent: read-only on prefs). A non-empty result is surfaced in the Monday note's
"a few things" tier in plain English ("One of your saved settings is from an older version
— I can clear it next time we update") and is the signal that a release-manifest migration
should heal it. An empty result (the common case) is silent.

### 3b. Heal corruption (REMEDIATE — safe, automatic)

Malformed lines in `events.jsonl` (e.g. a sync hiccup that wrote half a record) are healed automatically every run via the **recurring** self-heal:

```
python3 shared/scripts/recover_corruption.py <workspace_root> --recurring
```

`--recurring` triggers on "is anything broken right now?" (not once-per-version), so it catches drift that accumulates between upgrades. It quarantines only the malformed lines to `_hq/.system/quarantine/` (saved, never deleted), rewrites events.jsonl without them (atomic), appends a `corruption_recovery` event, and returns a friendly `customer_message`. A clean file is a fast no-op (nothing written). Surface its `customer_message` in the Monday note only when it actually healed something.

### 3c. Auto-fix the safe referential findings

For inspector findings that are unambiguous and non-destructive, fix them and record into `actions_taken[]`:
- **Phantom tracker entry** (tracker row, no folder) → remove the stale pointer (note it).
- **Missing PROJECT_BRAIN on a real project folder** (`C11.missing_brain`) → backfill a scaffold brain from session notes (maintenance-rules Rule 15).
- **Missing SESSION_NOTES on a real project folder** (`C11b.missing_session_notes`, D3) → scaffold a session-notes file from `references/session-notes-template.md` via the helper below. **NEVER overwrites an existing notes file** — the helper refuses if any `SESSION_NOTES*.md` already exists, so a client's real notes are safe. Mirrors the brain backfill (Rule 16 in maintenance-rules).
- **Dead alias** pointing at an archived/nonexistent entity → prune the alias.
- **Stale PROJECT_CONTEXT** → regenerate from session notes where the source is clearly present.

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import integrity_check as ic, cleanup_actions as ca
ws = '<workspace_root>'
created = []
for f in ic.scan_project_structure(ws):
    if f.check == 'C11b.missing_session_notes':
        path = ca.backfill_session_notes(ws, f.subject)  # None if notes already exist (never overwrites)
        if path: created.append(path)
print(json.dumps({'session_notes_backfilled': created}))
"
```

> **Orphan folders are FLAGGED, not moved** (revised for client safety, D1). On the 5 live client workspaces, a folder with no thread record is almost always a hand-made folder the CEO created deliberately — auto-archiving it to `_archive/` would be destructive from their point of view. Surface orphans in the Monday note (Phase 3d / Beat 1) with a one-line "register or archive?" prompt; let the CEO decide. Only archive an orphan when the CEO explicitly says so.

### 3d. Flag — never silently mutate — the unsafe ones

These go to `items_flagged_for_user[]` and the Monday note, NOT auto-fixed:
- **Orphan folders** (`C10.orphan_folder`) — folder on disk with no thread record. FLAG with a "register or archive?" prompt; never move or delete (client safety — see 3c note).
- **Duplicate event seqs** — valid JSON with colliding numbers. Append-only history must NOT be rewritten (it would break the tamper-detection hash chain). These require a deliberate additive correction (a dedicated converter), so cleanup only *reports* them.
- **Org/thread parent cycles**, unresolved affiliations on active orgs, and any ambiguous referential break that needs a human judgment call.

### 3d-bis. Writer-Contract lint (event-write path — SPEC GATE1)

The old Writer-Contract check only confirmed a SKILL.md carried the `## Writer Contract` **header** (`WORKSPACE_API.md` §"How Skills Reference This File"). A header is not a write path: a skill could carry the boilerplate header and still hand-roll a `next_seq`+`open('a')` append that dodges the A1 writer lock (decision-log was the confirmed bypass). This step runs the executable lint that asserts the BODY of every event-appending skill names the locked writer `atomic_append_jsonl` (or a known append-routing helper script that does).

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import writer_contract_lint as wcl
findings = wcl.lint_skill_event_writes('.')   # plugin root = cwd ($PLUGIN_ROOT)
print(json.dumps({'count': len(findings), 'skills': [f['skill'] for f in findings]}))
"
```

FLAG only — never auto-edit a SKILL.md. If the count is non-zero, add ONE plain-English line to the Monday note's "worth a glance" tier (no `_hq/` paths, no `atomic_append_jsonl` jargon, no skill names): *"Part of Command Room is saving your activity log an older way — nothing's broken, but it's worth mentioning to whoever set up your Command Room."* A clean tree (count 0) adds nothing.

In the same pass, run the seq-prestamp lint (`wcl.lint_seq_prestamp('.')`) — it flags any script or skill prose that reserves a seq via `next_seq()` and hand-stamps `"seq"` on an event (BUG-8330 item 7: the appender allocates seq inside the writer lock; a peeked value is the duplicate-seq race). Same flag-only handling, same single Monday-note line.

### 3d-ter. Duplicate-seq detector (BUG-8330 item 7c, recurring)

Historic duplicate seqs (pre-atomic-write window) are live ambiguity — a seq-alias closure resolves to EVERY commitment at that seq. This step detects and marks them additively; history is never rewritten:

```bash
mkdir -p "<workspace_root>/_hq/tmp"
DUP_REPORT_FILE="<workspace_root>/_hq/tmp/seq_health_dup_report.json"
python3 shared/scripts/seq_health.py "<workspace_root>" --mark | tee "$DUP_REPORT_FILE"
```

The `--mark` run appends one `seq_repaired` marker per NEWLY-found duplicated seq (the marker is the detector's own memory, so a known duplicate is never re-reported). If `n_new` is non-zero, add ONE plain-English line to the Monday note's "worth a glance" tier: *"Found N activity-log entries sharing a record number — marked them so they can't be confused for each other. New ones appearing would mean something's writing the log wrong; mention it to whoever set up your Command Room."* `n_new: 0` adds nothing. **This run's own JSON report is saved to `$DUP_REPORT_FILE`** (fix round 2, F-8) so rule 3d-quinquies below can read the SAME run's `n_new` instead of invoking `--mark` a second time — a second invocation would see every duplicate here as "already marked" (this run just marked it, moments ago) and would always report `n_new: 0`, even on the one week a duplicate is genuinely new. **The report file lives under the workspace itself, `<workspace_root>/_hq/tmp/`** (fix round 3, review finding F-12, MED) — not under `${CLAUDE_CODE_TMPDIR:-/tmp}` as fix rounds 1–2 had it. `_hq/tmp/` is already one of this plugin's own listed internal scratch paths (`CONTRACT.md`); resolving the SAME literal `<workspace_root>`-relative path in both this bash block and 3d-quinquies' below means there is exactly ONE path resolution shared by both halves, not two independent ones that can disagree. The two independent ones DID disagree on a Windows dev box with `CLAUDE_CODE_TMPDIR` unset: bash's `${CLAUDE_CODE_TMPDIR:-/tmp}` resolves through the MSYS mount to the real filesystem temp directory, while the embedded Python's `open('/tmp/...')` resolves the same literal string relative to the current drive instead — two different files, every single fire, so 3d-quinquies' read below always failed on that path. A workspace-relative path has no environment variable to disagree about.

### 3d-quater. Missing-entry detector (LEDGERFENCE1, recurring — CONTRACT Rule 31)

A duplicate entry number means two events were written with the same number. A HOLE is the opposite: numbers that are not in the file at all (2026-09-07: a chat cleaning up its own re-run backed the log up and deleted twelve lines, leaving 15555 → 15568; the health check said nothing because it read duplicates and not holes). **Not every hole is a removal** — `scan_gaps` classifies each one, and only the class it cannot explain (`removed`) is ever spoken about: a re-based counter, numbers one append took and did not use, the product's own recorded repair work, and numbers taken by a run whose lines never reached this copy are all named and none of them reach the note. Same detect-mark-report shape, same append-only posture — the marker is a memory, it does not touch the hole:

```bash
python3 shared/scripts/seq_health.py "<workspace_root>" --gaps --mark
```

The `--gaps --mark` run appends one `seq_gap_marked` marker per NEWLY-found stretch of missing entry numbers, WHATEVER its class (`detect_and_mark_gaps`), so a known stretch is never re-reported and no line repeats the following Monday. If `n_new` is non-zero, add the report's own `notice` string — `seq_health.gap_notice(report)`, composed in code and relayed VERBATIM, never re-worded, never trimmed to one class — to the Monday note's "worth a glance" tier. It is one plain sentence per class present: a removal first, then a repair, then a restarted numbering. `n_new: 0` adds nothing. Never try to fill a stretch, restore a backup, or reconstruct the missing entries: they are gone, and the only honest act is to say so.

### 3d-quinquies. The morning brief's health block, relocated here (HEALTH1, 2026-09-07)

**M ruled, reading his own morning brief:** *"this should not be shown."* The substrate-alarm line, the daily watchdog pointer, the dark-surface line and the schedule-refresh announcement used to render on the morning brief (CUT-PLATE moved them from the top to the end; M's ruling takes them off that surface entirely, in full, for all four kinds). None of it goes unreported: this weekly pass is now the ONLY place any of the four render, and the only surface where a cleanup pass over the finding can actually be offered and run. Three of the four are new here; the watchdog finding was already covered in more detail by 3e-bis below (no change needed there beyond this citation). (Renamed from `3d-quater` to `3d-quinquies` at the LEDGERFENCE1 merge seam — that lane's own missing-entry detector lands first and keeps the `3d-quater` number; see that section elsewhere in this same maintenance pass. **Fix round 3 (review finding F-13, MED, at-merge).** This used to say "immediately above," which a clean auto-merge is not guaranteed to preserve — trial-merging this branch onto LEDGERFENCE1's current tip lands the sections `3d-ter → 3d-quinquies → 3d-quater → 3e`, with LEDGERFENCE1's section AFTER this one, not above it, and nothing reds to flag the mis-citation. The target order at merge is `3d-ter → 3d-quater → 3d-quinquies → 3e` — reorder by hand; no conflict prompts it. This citation no longer claims a position, so it stays true either way.)

**Fix round 1 (review finding F-1, HIGH).** The first cut filtered the duplicate-entry line out of `alarms` UNCONDITIONALLY, on the premise that Rule 3d-ter's own report always speaks for that exact condition. It does not: 3d-ter's `n_new` counts only NEWLY-found duplicates on THIS run — a duplicate is marked the first week it is seen, and every week after that `n_new` is 0 and 3d-ter renders nothing, while the standing condition (two records still sharing one number) has not gone anywhere. On M's own 2026-09-07 book this is exactly seq 14391: marked once, `n_new: 0` on every later run, and the line was reported on NO scheduled surface at all — not the brief, not the day-close, not this weekly note — leaving only the on-demand `health check` to say it. The fix below reads the SAME run's `n_new` that 3d-ter just reported (passed through the `DUP_N_NEW` environment variable, never string-interpolated into the python source — the guard that census-scans every embedded snippet parses this block as real Python, and a shell substitution spliced into the middle of it would not parse) and filters the duplicate-entry line only when 3d-ter genuinely spoke this week (`n_new > 0`, so the two would otherwise say the same thing twice); every other week — the standing case, which is most weeks — the line renders here verbatim, because this is the only place left that still says so.

**Fix round 2 (review finding F-8, MED).** Fix round 1's own wiring re-ran `seq_health.py --mark` a SECOND time here to get `DUP_N_NEW`, rather than reading 3d-ter's report — and a second `--mark` invocation always sees this run's duplicate as already marked (the FIRST invocation, in 3d-ter above, just marked it, moments earlier in the same fire), so `DUP_N_NEW` was pinned at 0 every run and the conditional filter could never fire in a real fire: on the one week a duplicate is genuinely new, 3d-ter's own line ("Found N activity-log entries…") rendered AND this rule's unconditional-in-practice fallthrough rendered the standing line too — the same condition, said twice, in two voices, in the same note. **`seq_health.py --mark` is invoked exactly ONCE per run, in 3d-ter above; this rule reads that SAME run's report from the file 3d-ter saved it to, never re-invoking the detector.**

**Fix round 3 (review finding F-12, MED).** Fix round 2's own read had no `try`: an absent, empty, truncated, or key-less report file made `json.load(...)['n_new']` raise, the command substitution below it silently captured the EMPTY STRING, `export DUP_N_NEW=` set the variable to that empty string, and the composing block's `int(os.environ.get('DUP_N_NEW', '0'))` then raised on `int('')` — BEFORE printing anything — so the WHOLE relocated block (alarms, dark-surface, schedule-refresh, all three) rendered nothing at all, silently, on the one surface that still carries any of it. Proven four ways (the file absent; empty; truncated JSON; valid JSON missing the `n_new` key) and by two real routes in (3d-ter's own `seq_health.py --mark` dying on a non-UTF-8 ledger leaves a 0-byte report via `tee`; a stale report left over from an earlier week when this run's 3d-ter is skipped). The read below is now total — every one of those four shapes resolves to the plain sentinel `MISSING` instead of raising — and a `MISSING` read renders the rest of the block exactly as before PLUS one honest sentence saying the duplicate check itself did not run this time, rather than crashing the whole thing into silence. `MISSING` also folds `dup_n_new` to `0`, the same safe direction fix round 1 already established for a standing, already-marked duplicate — the duplicate-entry line still renders from `substrate_health`'s own independent, always-live read of the ledger, which does not depend on this file at all.

```bash
# Read THIS SAME run's `--mark` report from the file 3d-ter above saved it
# to — NEVER invoke `seq_health.py --mark` a second time here. A second
# invocation would always see the duplicate as "already marked" (3d-ter's
# own invocation, moments ago, in the SAME run) and would report `n_new: 0`
# even on the one week a duplicate is genuinely new — the exact hole fix
# round 2 (review finding F-8) found: the standing line and 3d-ter's own
# new-finding line both rendering, saying the same thing twice.
DUP_REPORT_FILE="<workspace_root>/_hq/tmp/seq_health_dup_report.json"
# Fix round 3 (review finding F-12): a TOTAL read. An absent, empty,
# truncated, or key-less report file must never raise here — the failure
# used to crash this whole block (alarms, dark-surface AND
# schedule-refresh) into silence, not merely the duplicate-entry line.
export DUP_N_NEW=$(python3 -c "
import json
try:
    print(json.load(open('$DUP_REPORT_FILE'))['n_new'])
except Exception:
    print('MISSING')
")
python3 -c "
import sys, os, json; sys.path.insert(0, 'shared/scripts')
from substrate_health import substrate_alarm_lines
from task_alarm import dark_surface_lines
from schedule_refresh import announce_lines
from writer_gate_report import report_lines as writer_gate_lines
ws = '<workspace_root>'
# Fix round 3 (F-12): DUP_N_NEW is always exported as either a real int or
# the sentinel 'MISSING' — never empty — so this never raises. MISSING
# folds to the safe fallback (0, never filter) and adds one honest line
# instead of letting the whole block die silently.
_dup_raw = os.environ.get('DUP_N_NEW', 'MISSING')
dup_ter_ran = _dup_raw != 'MISSING'
dup_n_new = int(_dup_raw) if dup_ter_ran else 0
raw_alarms = substrate_alarm_lines(ws)
if dup_n_new > 0:
    alarms = [l for l in raw_alarms if 'duplicate entry number(s)' not in l]
else:
    alarms = raw_alarms
if not dup_ter_ran:
    alarms = alarms + ['This weekly duplicate-record check did not run this time — nothing new to report on it.']
dark = dark_surface_lines(ws)
# Fix round 1 (review finding F-6, LOW): BRIDGESIL1's render-once ledger
# keys on event seq, not sentence content, so two separate schedule-change
# events that land on the identical announce sentence both survive its own
# dedup and would otherwise be said twice in the SAME note — worse here
# than on a daily surface, where the doubled line was only ever one day's
# noise. Dedupe by TEXT here, once, without touching BRIDGESIL1's own
# ledger (that stays a per-event memory; the source-level fix is
# BRIDGESIL1's owner's, not built on this branch).
refresh = list(dict.fromkeys(announce_lines(ws)))
# OUTGATE1 (2026-09-08) — the writer-path gate's own self-report: results per
# surface with a resultless gate_ran counted as a defect, plus a gated
# writer surface (docx / premium_html / chat_email) quiet 48h+.
# Maintenance-run only, never the brief or the day-close (see
# tests/run_guard_writer_gate_not_on_brief_test.py) — no calling convention
# differs from `dark_surface`/`schedule_refresh` above: computed here, folded
# into this SAME note, never read anywhere a customer surface calls back
# into.
writer_gate = writer_gate_lines(ws)
print(json.dumps({'alarms': alarms, 'dark_surface': dark,
                  'schedule_refresh': refresh, 'writer_gate': writer_gate}))
"
```

- **`alarms`** — every returned line renders verbatim in the Monday note's "worth a glance" tier, most-severe first: the log-clobber, stale-view, unreadable-JSON, read-time-corruption and forward-dated-stamp lines FS-04/05/06/15 + CLOCKTS1 exist for. **The duplicate-entry line is filtered out of this list ONLY when 3d-ter's `n_new` from THIS SAME run was greater than 0** (fix round 1, F-1) — rule 3d-ter above (`seq_health.py --mark`) speaks for the condition exactly once, the week it is newly found; every week after that the marker means "known", not "resolved", and this line is the only surface left that still says so, verbatim. Rendering both in the same run would say it twice in two voices; rendering neither, forever, after the first week, is the bug this round closes. Empty list → nothing renders, never a padded all-clear. **`n_new` comes from 3d-ter's OWN report file, never a second `--mark` call** (fix round 2, F-8) — `seq_health.py --mark` runs exactly once per fire, in 3d-ter above; a second invocation here would always report `n_new: 0` (the first invocation just marked the duplicate, moments ago in the same run) and would say the condition twice, in two voices, on the one week it is genuinely new. **The report-file read never raises** (fix round 3, F-12) — an absent, empty, truncated, or key-less file folds to a `MISSING` sentinel, `dup_n_new` falls back to `0` (the safe direction — the standing line still renders), and one line saying the check did not run this time is added instead of the entire block crashing into silence.
- **`dark_surface`** — `task_alarm.dark_surface_lines`'s own render-once ledger (TASKALARM1), now consumed HERE instead of on the brief or the day-close (both retired their calls to it — HEALTH1 is not only a print change, it moves which surface calls the helper, because calling-and-discarding would have marked the finding "seen" before this weekly pass ever ran). Fold its lines in beside the watchdog findings below — a task already named by 3e-bis is never named twice in the same note. **The circularity this creates (review finding F-3, MED):** after this lane the ONLY scheduled thing that can say "your Maintenance task stopped firing" is this very maintenance run — if THIS is the dark surface, nothing on a schedule says so, and the on-demand `health check` is the only remaining route. M has this as an open ruling (R-2 in the review; recommendation on file: a plain one-sentence exception on the brief when the weekly maintenance itself has not run, never the full health block back). Not built on this branch pending that ruling.
- **`schedule_refresh`** — `schedule_refresh.announce_lines`'s own render-once ledger (BRIDGESIL1): the ONE narration for a silently-applied semantic schedule change since the last time anything read it, DE-DUPED BY TEXT here (fix round 1, F-6) so two events landing on the identical sentence are never both said in the same note. Same reasoning as `dark_surface` otherwise — the brief no longer calls this helper either, so this weekly pass is its sole reader now.
- **`writer_gate`** — OUTGATE1 (2026-09-08). `writer_gate_report.report_lines` censuses the `gate_ran` events the writer chokepoints (`brief_gates` for `docx`/`premium_html`, `turn_backstop` for `chat_email`) already emit and turns two conditions into plain lines: a resultless `gate_ran` this week (the gate ran and forgot to say pass/fail — a defect in the gate itself) and a gated surface quiet 48h or more after having fired before (dark, not merely idle — a surface that has never fired at all is never alarmed, permanently: there is no registration moment to anchor a grace period to for a writer surface the way `task_alarm` has for a scheduled task, so alarming on never-fired would light up any workspace that simply doesn't use one of the gated document kinds). A surface only belongs in that gated set if something in `shared/scripts/` can actually emit it — `run_guard_writer_gate_not_on_brief_test.py` section [4] reds otherwise, so this note can never carry a line no amount of correct behaviour would clear (FIX ROUND 2, review finding F-8). Closes `BUG_2026-08-04_no-alarm-when-scheduled-surfaces-stop` for the WRITER-GATE class of surface (`task_alarm`/`dark_surface` above already covers the SCHEDULED-TASK class) and retires the per-turn `Stop`-hook sweep this same lane deleted (`hooks/hooks.json` + `gate2_turn_sweep.py` — M ruled 2026-09-07 Cowork never runs plugin hooks; `BUG_2026-09-07_turn-hook-silent-and-gates-disagree` is why: the hook silently stopped emitting for two weeks and disagreed with the docx surface while it ran). Maintenance-run only, never the brief or the day-close — `tests/run_guard_writer_gate_not_on_brief_test.py` is the structural + behavioral fence for that boundary, INCLUDING (FIX ROUND 1, F-1) a positive check that this very fence below is genuinely wired, not merely absent from the brief. Empty list → nothing renders, same all-clear convention as every line above it.

### 3d-sexies. The plumbing's own week, and the workspace's own record quality

Two conditions the morning brief used to carry and no longer does. **NUMBER1 3.9 (M's 2026-09-07 ruling and its extension):** a customer surface reports the customer's work; the condition of the machinery underneath is reported here, on the run that can actually offer and perform a cleanup. They are moved, not deleted — a line reported nowhere is a worse fix than the defect.

```bash
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json, os
sys.path.insert(0, os.path.join(os.getcwd(), 'shared', 'scripts'))
ws = os.environ['WORKSPACE_ROOT']
import change_feed
from datetime import datetime, timedelta, timezone
since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
feed = change_feed.changes_since(ws, since)
out = {'plumbing': change_feed.plumbing_lines(feed)}
# Routed in from ONBOARDGUARD1 (night 11d): the scope-hygiene note. A
# READ, never a write to entities.json, and the line carries no company
# name. Empty string renders nothing.
import onboarding_profile
out['scope_hygiene'] = onboarding_profile.scope_hygiene_line(ws) or ''
print(json.dumps(out))
"
```

- **`plumbing`** — `change_feed.plumbing_lines` returns the week's cleanup-pass and background-maintenance sentences ("Ran the weekly cleanup pass.", "Completed N background maintenance jobs on schedule."). Render each verbatim in the Monday note's "worth a glance" tier. `surface_drivers.brief_plumbing_categories` drops the same set from the brief's CHANGED strip and reads it from the same constant, so the surface that refuses them and this note that claims them can never disagree about which sentences those are. Empty list renders nothing — never a padded all-clear.
- **`scope_hygiene`** — routed in from ONBOARDGUARD1. One plain line when the workspace's own org records carry a missing or invalid `scope`, in the "worth a glance" tier, with no company name in it and no write of any kind. Empty string renders nothing. This is a data-quality fact about the record, which is why it belongs on this surface and on no customer one.

- **The seq-GAP check is a separate hole, already landed by LEDGERFENCE1** in its own `3d-quater` section of this same maintenance pass (fix round 3, review finding F-13: worded without an adjacency claim — see the heading above for why "immediately above" no longer holds after a clean merge) (`seq_health.gap_notice(report)`, composed in code and relayed verbatim). Nothing further to wire from this lane. **Correction (fix round 2, F-10):** the parity with this rule's own duplicate-entry filter claimed above ("never told twice") does not hold. LEDGERFENCE1's `gap_notice` only ever speaks about a stretch the run it fires in newly marks (`detect_and_mark_gaps`'s own `new` list) — once a stretch is marked, `substrate_health.py` gains no standing gap line the way it carries the standing duplicate-entry line via `substrate_alarm_lines`, so a marked hole is said once, the week it is found, and then by nothing on any scheduled surface, ever again. That is this rule's own F-1 shape, one detector over, still open at LEDGERFENCE1's tip as of this writing (confirmed against `~/repos/wt-ledgerfence1` @ `532358f2f9a373230ed332c2cdb08bdbf6e237a5`, its current HEAD — that lane's own separately-numbered "FIX ROUND 2 (F-10)" addressed a different gap, that only the `removed` class rendered at all; it did not address the standing-report question). **Fix round 3 addendum (F-13):** that same `532358f2` tip's `gap_notice` returns `""` whenever `report["new"]` is empty, and `detect_and_mark_gaps(apply=True)` marks every stretch on its first sight — so from week two onward `n_new` is 0 and the notice is empty, on BOTH of that lane's own reporting surfaces (cleanup's Monday note and `system-health`'s on-demand report), forever. That is worse than this rule's own F-1 was: F-1 at least kept the on-demand `health check` route; LEDGERFENCE1's gap hole has no escape hatch at all, scheduled or on demand. Not this lane's branch to fix — flagged to LEDGERFENCE1's owner and to M as an at-merge item, the same way review finding F-10 asked.

### 3e. Append the run record + conflicts

- Append schema/integrity violations to `_hq/CONFLICTS.md` (same as the old audit).
- Append ONE `cleanup_run` receipt via the canonical helper (`shared/scripts/receipts.py`, v4.5.2 R1) — **this receipt is REQUIRED on every run, even a nothing-to-do run**: cleanup fired receiptless for ~6 weeks during the v4.5.1 dogfood and its silent failures were indistinguishable from silent successes (FINDINGS F-39/F-43/F-54). One line: `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "cleanup", receipt_type="cleanup_run", fired_via="scheduled", extra_data={"actions_taken": [...], "items_flagged_for_user": [...], "tail_hash": "...", "dualkey1_repair": {"n_merged": <int>, "n_quarantined": <int>, "n_deleted_keys": <int>}})` — `"manual"` for fired_via on `run cleanup` chat fires. `dualkey1_repair` carries the three counts from Phase 3.5a-bis verbatim, on EVERY run (zero-written, never omitted — see that phase for why).
- **tail_hash backward compatibility:** when computing the append-only mutation check, look up the previous run's `tail_hash` from the most recent `cleanup_run` **OR** legacy `audit_run` event (accept either type). New events are always written as `cleanup_run`. Never rewrite old `audit_run` events.

## Phase 3.5: Brain self-heal (Live State render + one-time migration)

The brain-substrate fleet backstop — this is what makes the weekly schedule earn its keep. Per-project `PROJECT_BRAIN` files keep their People + Status sections rendered live from the substrate. The `go [project]` load-path already refreshes a project the moment the CEO opens it; this weekly sweep covers the projects they did NOT open, so nothing silently goes stale.

### 3.5a — One-time migration (idempotent, never destructive — safe to run every week)
Convert each project's hand-written People table into the generated Live State block. The helper is idempotent — a no-op after the first conversion — so it's safe to call every run.

**Hard gate (v3.18.2+ — Bug #84). Run the EXACT block below; do not pre-flight it.** The migration script ships at `shared/scripts/release_actions/migrate_brain_live_state.py` in **every v3.16+ build** — it is NOT optional and NOT version-gated. Do NOT check "does this version have the script" and skip; do NOT guess the path (`shared/scripts/migrate_brain_live_state.py` — without `release_actions/` — is the WRONG path and is what the v3.18.1 scheduled fire mis-resolved before logging "doesn't exist in this version — skipped gracefully"). The block resolves `PLUGIN_ROOT` explicitly (so it does not depend on the current working directory at fire time) and **assert-imports** the module: if the import raises, that is a LOUD, real failure (incomplete plugin install) to surface in the run record — **never** a silent "feature not in this version" skip.

```bash
# >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
# The substrate is on the customer's machine; this process may not be. Every
# read, helper and write goes through workspace_access ON the host that holds
# the data. Never open, copy or tar a workspace file into this session, and
# never write one from here.
#  1 RESOLVE, once per call. The four lines below name the plugin root, the
#    environment, and -- on a seat whose files are local -- the workspace. On a
#    merged seat resolve instead with `workspace_access.py discover`, hand the
#    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
#    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
#    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
#    before the first plan, so a saved document can name the folder the
#    customer opens. A runtime that is absent, or a runtime_version that
#    differs, is a STOP: run the update-bridge install step. There is no
#    container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
#    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
#    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
#    variables in front of python3, which carry the writer identity and the
#    run mode to the host that holds the data. The reply is one JSON
#    envelope; ok:false is a stop, never a hand retry.
#  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
#    redirect, an in-place edit, a heredoc into the workspace, or a python body
#    that opens a substrate file.
#  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
#    sandbox seat, or a Code session on the customer's own machine -- the same
#    verbs run in this shell, and the four lines below resolve it for them.
#  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
#    sentence with no file, script, path, variable, shell text or mechanism
#    in it -- "One step could not run here; what is below is complete." or
#    "... is partial." Never narrate a workaround, never say what you tried.
#  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
#    render -- lives in this session's own scratch, never under the
#    workspace. Nothing under `_hq/` is created, copied or removed by a
#    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
#    removed by `plan remove`, and a removal is reported in the envelope's
#    own words -- removed, moved aside, or still there -- never as done.
#  8 WRITERS. A document, a receipt, a close or a re-pin is written by
#    `plan run_writer` naming a writer on its list -- never by importing a
#    writer in a shell. The door forwards who you are; a writer with no
#    identity on this seat refuses in one sentence, and that sentence is the
#    whole answer.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json, os
root = os.getcwd()
# Absolute sys.path — independent of cwd at fire time (Bug #84 root cause).
sys.path.insert(0, os.path.join(root, 'shared', 'scripts'))
sys.path.insert(0, os.path.join(root, 'shared', 'scripts', 'release_actions'))
ra = os.path.join(root, 'shared', 'scripts', 'release_actions', 'migrate_brain_live_state.py')
# Assert-import — fail LOUD, never 'skip gracefully'. Ships in every v3.16+ build.
try:
    import migrate_brain_live_state as m
    import render_thread_live_state as r
except ImportError as e:
    raise SystemExit('ABORT Phase 3.5a — could not import migrate_brain_live_state from '
                     + ra + ': ' + repr(e) + '. The plugin install is incomplete; this is a REAL '
                     'error to surface, NOT a missing-feature skip.')
assert hasattr(m, 'migrate_brain'), 'migrate_brain_live_state imported but has no migrate_brain() — stale/corrupt build; ABORT (do not skip)'
ws = '<workspace_root>'
# Shape-defensive entities read (Bug #84-followup, found 2026-05-31 in A84 verify):
# many real workspaces (M's included) store entities FLAT (threads at top level, no
# 'entities' wrapper). The old wrapper-only read returned an empty dict on a flat file
# -> 0 threads -> migration silently processed nothing (the #84 outcome via a 2nd cause).
_d = json.load(open(ws + '/_hq/data/entities.json'))
ent = _d['entities'] if isinstance(_d.get('entities'), dict) else _d
# SPEC DUALKEY1: single canonical read — entities_collection aliases the
# retired 'projects' spelling to the canonical `threads` collection.
from entities_io import entities_collection
threads = entities_collection(ent, 'projects')
migrated, errors, skipped = [], [], []
for t in threads:
    # FOLDERGUARD: terminal threads are not migrated. This loop had NO status
    # filter at all, so it touched every thread ever created — a wider scope than
    # the C9 checker, which only inspects non-archived ones. Matches the codebase
    # terminal pair (deal_state.py:186, objective_state.py:621).
    if t.get('status') in ('resolved', 'archived'): continue
    bp = r.default_brain_path(ws, t['id'])
    # None now also means 'folder_name names no real directory' — do NOT write
    # there. Writing was what fabricated the folder and then hid the C9 finding.
    if not bp:
        skipped.append(t['id']); continue
    try:
        # Count only brains actually CHANGED, never merely visited — migrate_brain
        # returns {'changed': bool} and is a no-op after the first conversion.
        # Counting visits is what let a fabricated folder show up in actions_taken[].
        if m.migrate_brain(ws, t['id'], bp, dry_run=False).get('changed'):
            migrated.append(t['id'])
    except Exception as e:
        errors.append((t['id'], repr(e)))  # per-thread, surfaced — not silently swallowed
print('migrated/verified', len(migrated), 'brains;', len(skipped),
      'skipped (no resolvable folder); per-thread errors:', len(errors))
if errors: print('PER-THREAD-ERRORS:', errors)
if skipped: print('NO-FOLDER-SKIPPED:', skipped)
"
```
Two distinct failure modes: a **missing/broken module** is a loud ABORT (the assert-import above) — surface it, never skip Phase 3.5a; a **per-thread** exception is collected and surfaced in the run log but does not abort the sweep. `migrate_brain` **NEVER deletes a hand-written person** — anyone with no events relocates to a "Manually tracked" durable list, never dropped. Record into `actions_taken[]` only the brains it actually changed.

### 3.5a-bis — Repair the dual project key (idempotent — SPEC DUALKEY1)

Runs BEFORE 3.5b's Live State re-render, so a workspace that still carries the vestigial `projects` key gets its threads merged and de-duped before anything downstream iterates them. A pre-DUALKEY1 `entities.json` can carry BOTH the canonical `threads` collection and a `projects` key created the instant any older reader called the pre-alias `entities_collection("projects")`, which minted a SECOND, separately-writable list. Left unrepaired, one stray record under `projects` silently drops every dual-key reader in the product from the real thread count to just that one record. This is the weekly backstop; Phase 4.4b of `command-room-update-bridge` delivers the same repair immediately at update time so most workspaces never reach this fire with anything to do.

Safe and idempotent by construction: non-empty `projects` records merge into `threads` deduped by id (an id already in `threads` keeps the `threads` copy; the `projects` duplicate is quarantined under `_recovery`, never dropped), then the `projects` key is deleted. A workspace with no `projects` key at all is a true no-op — zero writes.

```bash
# >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
# The substrate is on the customer's machine; this process may not be. Every
# read, helper and write goes through workspace_access ON the host that holds
# the data. Never open, copy or tar a workspace file into this session, and
# never write one from here.
#  1 RESOLVE, once per call. The four lines below name the plugin root, the
#    environment, and -- on a seat whose files are local -- the workspace. On a
#    merged seat resolve instead with `workspace_access.py discover`, hand the
#    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
#    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
#    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
#    before the first plan, so a saved document can name the folder the
#    customer opens. A runtime that is absent, or a runtime_version that
#    differs, is a STOP: run the update-bridge install step. There is no
#    container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
#    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
#    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
#    variables in front of python3, which carry the writer identity and the
#    run mode to the host that holds the data. The reply is one JSON
#    envelope; ok:false is a stop, never a hand retry.
#  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
#    redirect, an in-place edit, a heredoc into the workspace, or a python body
#    that opens a substrate file.
#  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
#    sandbox seat, or a Code session on the customer's own machine -- the same
#    verbs run in this shell, and the four lines below resolve it for them.
#  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
#    sentence with no file, script, path, variable, shell text or mechanism
#    in it -- "One step could not run here; what is below is complete." or
#    "... is partial." Never narrate a workaround, never say what you tried.
#  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
#    render -- lives in this session's own scratch, never under the
#    workspace. Nothing under `_hq/` is created, copied or removed by a
#    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
#    removed by `plan remove`, and a removal is reported in the envelope's
#    own words -- removed, moved aside, or still there -- never as done.
#  8 WRITERS. A document, a receipt, a close or a re-pin is written by
#    `plan run_writer` naming a writer on its list -- never by importing a
#    writer in a shell. The door forwards who you are; a writer with no
#    identity on this seat refuses in one sentence, and that sentence is the
#    whole answer.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
cd "$PLUGIN_ROOT" && python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from thread_writer import repair_dual_project_key
counts = repair_dual_project_key('<workspace_root>', source_skill='cleanup')
print('n_merged=' + str(counts['n_merged']))
print('n_quarantined=' + str(counts['n_quarantined']))
print('n_deleted_keys=' + str(counts['n_deleted_keys']))
"
```

**Stamp all three counts on the Phase 3e `cleanup_run` receipt's `extra_data`, every run, zero-written and never omitted** — `extra_data.dualkey1_repair = {"n_merged": <int>, "n_quarantined": <int>, "n_deleted_keys": <int>}`, even when every count is 0. A receipt that only appears when there was something to report is indistinguishable from a fire that skipped the step; a receipt that always carries the field is proof the step ran. Record into `actions_taken[]` only when `n_merged` or `n_quarantined` is greater than 0 — a quiet workspace (no `projects` key, or the key was already gone) stays quiet (HONEST1, same reporting rule as 3.5b below).

### 3.5a-ter — Heal off-enum thread kinds (idempotent — SPEC THREADBIND1 §0 ruling 4)

Runs right after 3.5a-bis, same DUALKEY1-style shape: a pure mutator plus an owner-writer wrapper that persists only when something actually changed. The thread `kind` vocabulary closed under THREADBIND1 (`thread_writer.VALID_KINDS`, mirrored in `entities.schema.json` $defs.project.kind) — `create_thread` / `update_thread` now reject a novel kind at write time, but a THREAD ALREADY ON DISK from before the enum closed can carry a KNOWN off-enum spelling (today: `product_build`, a typo-split of `product`). This step heals those in place, additive: `thread_writer.KIND_MIGRATION_MAP` gets a new line the day a new drifted spelling is confirmed — this step never guesses at one on its own.

```bash
# >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
# The substrate is on the customer's machine; this process may not be. Every
# read, helper and write goes through workspace_access ON the host that holds
# the data. Never open, copy or tar a workspace file into this session, and
# never write one from here.
#  1 RESOLVE, once per call. The four lines below name the plugin root, the
#    environment, and -- on a seat whose files are local -- the workspace. On a
#    merged seat resolve instead with `workspace_access.py discover`, hand the
#    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
#    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
#    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
#    before the first plan, so a saved document can name the folder the
#    customer opens. A runtime that is absent, or a runtime_version that
#    differs, is a STOP: run the update-bridge install step. There is no
#    container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
#    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
#    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
#    variables in front of python3, which carry the writer identity and the
#    run mode to the host that holds the data. The reply is one JSON
#    envelope; ok:false is a stop, never a hand retry.
#  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
#    redirect, an in-place edit, a heredoc into the workspace, or a python body
#    that opens a substrate file.
#  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
#    sandbox seat, or a Code session on the customer's own machine -- the same
#    verbs run in this shell, and the four lines below resolve it for them.
#  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
#    sentence with no file, script, path, variable, shell text or mechanism
#    in it -- "One step could not run here; what is below is complete." or
#    "... is partial." Never narrate a workaround, never say what you tried.
#  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
#    render -- lives in this session's own scratch, never under the
#    workspace. Nothing under `_hq/` is created, copied or removed by a
#    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
#    removed by `plan remove`, and a removal is reported in the envelope's
#    own words -- removed, moved aside, or still there -- never as done.
#  8 WRITERS. A document, a receipt, a close or a re-pin is written by
#    `plan run_writer` naming a writer on its list -- never by importing a
#    writer in a shell. The door forwards who you are; a writer with no
#    identity on this seat refuses in one sentence, and that sentence is the
#    whole answer.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
cd "$PLUGIN_ROOT" && python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from thread_writer import repair_thread_kind_drift
counts = repair_thread_kind_drift('<workspace_root>', source_skill='cleanup')
print('n_migrated=' + str(counts['n_migrated']))
print('migrated=' + str(counts['migrated']))
"
```

**Stamp on the same Phase 3e `cleanup_run` receipt's `extra_data`, every run, zero-written and never omitted** — `extra_data.thread_kind_repair = {"n_migrated": <int>}`. Record into `actions_taken[]` only when `n_migrated` is greater than 0 (HONEST1, same reporting rule as 3.5a-bis and 3.5b) — list each `{"id", "from", "to"}` so the receipt names what changed.

### 3.5a-quater — Thread subject detection (idempotent — SPEC THREADANN1 §0 ruling 2)

Runs right after 3.5a-ter, over every non-archived thread: an evidence-scored
cluster detector (`shared/scripts/thread_subjects.py`) reads each thread's
bound events (org + attendee + commitment-text agreement, riding
THREADBIND1's `thread_basis` machinery) and, only when the evidence clears
the floor, ANNOTATES the thread with derived subject labels (`records never
move` — SPEC §0 ruling 1) and fires ONE propose-only "split it?" row through
the standing Living Brain queue (SPEC §0 ruling 4 — never auto-executed,
never nagged more than once per thread per 30 days). Below the floor: no
annotation, no proposal, no noise. Idempotent by construction: re-detecting
the same clustering writes nothing (HONEST1).

```bash
# >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
# The substrate is on the customer's machine; this process may not be. Every
# read, helper and write goes through workspace_access ON the host that holds
# the data. Never open, copy or tar a workspace file into this session, and
# never write one from here.
#  1 RESOLVE, once per call. The four lines below name the plugin root, the
#    environment, and -- on a seat whose files are local -- the workspace. On a
#    merged seat resolve instead with `workspace_access.py discover`, hand the
#    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
#    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
#    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
#    before the first plan, so a saved document can name the folder the
#    customer opens. A runtime that is absent, or a runtime_version that
#    differs, is a STOP: run the update-bridge install step. There is no
#    container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
#    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
#    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
#    variables in front of python3, which carry the writer identity and the
#    run mode to the host that holds the data. The reply is one JSON
#    envelope; ok:false is a stop, never a hand retry.
#  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
#    redirect, an in-place edit, a heredoc into the workspace, or a python body
#    that opens a substrate file.
#  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
#    sandbox seat, or a Code session on the customer's own machine -- the same
#    verbs run in this shell, and the four lines below resolve it for them.
#  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
#    sentence with no file, script, path, variable, shell text or mechanism
#    in it -- "One step could not run here; what is below is complete." or
#    "... is partial." Never narrate a workaround, never say what you tried.
#  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
#    render -- lives in this session's own scratch, never under the
#    workspace. Nothing under `_hq/` is created, copied or removed by a
#    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
#    removed by `plan remove`, and a removal is reported in the envelope's
#    own words -- removed, moved aside, or still there -- never as done.
#  8 WRITERS. A document, a receipt, a close or a re-pin is written by
#    `plan run_writer` naming a writer on its list -- never by importing a
#    writer in a shell. The door forwards who you are; a writer with no
#    identity on this seat refuses in one sentence, and that sentence is the
#    whole answer.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
cd "$PLUGIN_ROOT" && python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from thread_subjects import scan_workspace
counts = scan_workspace('<workspace_root>', source_skill='cleanup')
print('n_threads_scanned=' + str(counts['n_threads_scanned']))
print('n_annotated=' + str(counts['n_annotated']))
print('n_split_proposals=' + str(counts['n_split_proposals']))
"
```

**Stamp on the same Phase 3e `cleanup_run` receipt's `extra_data`, every run, zero-written and never omitted** — `extra_data.threadann1_scan = {"n_threads_scanned": <int>, "n_annotated": <int>, "n_split_proposals": <int>}`. Record into `actions_taken[]` only when `n_annotated` or `n_split_proposals` is greater than 0 (HONEST1, same reporting rule as 3.5a-bis and 3.5a-ter) — a workspace with no thread heavy enough to cluster stays quiet.

### 3.5b — Re-render every active thread's Live State (dirty-checked, cheap)
```bash
# >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
# The substrate is on the customer's machine; this process may not be. Every
# read, helper and write goes through workspace_access ON the host that holds
# the data. Never open, copy or tar a workspace file into this session, and
# never write one from here.
#  1 RESOLVE, once per call. The four lines below name the plugin root, the
#    environment, and -- on a seat whose files are local -- the workspace. On a
#    merged seat resolve instead with `workspace_access.py discover`, hand the
#    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
#    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
#    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
#    before the first plan, so a saved document can name the folder the
#    customer opens. A runtime that is absent, or a runtime_version that
#    differs, is a STOP: run the update-bridge install step. There is no
#    container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
#    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
#    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
#    variables in front of python3, which carry the writer identity and the
#    run mode to the host that holds the data. The reply is one JSON
#    envelope; ok:false is a stop, never a hand retry.
#  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
#    redirect, an in-place edit, a heredoc into the workspace, or a python body
#    that opens a substrate file.
#  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
#    sandbox seat, or a Code session on the customer's own machine -- the same
#    verbs run in this shell, and the four lines below resolve it for them.
#  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
#    sentence with no file, script, path, variable, shell text or mechanism
#    in it -- "One step could not run here; what is below is complete." or
#    "... is partial." Never narrate a workaround, never say what you tried.
#  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
#    render -- lives in this session's own scratch, never under the
#    workspace. Nothing under `_hq/` is created, copied or removed by a
#    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
#    removed by `plan remove`, and a removal is reported in the envelope's
#    own words -- removed, moved aside, or still there -- never as done.
#  8 WRITERS. A document, a receipt, a close or a re-pin is written by
#    `plan run_writer` naming a writer on its list -- never by importing a
#    writer in a shell. The door forwards who you are; a writer with no
#    identity on this seat refuses in one sentence, and that sentence is the
#    whole answer.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json, os
root = os.getcwd()
# Absolute sys.path + assert-import, matching Phase 3.5a. The relative
# sys.path.insert this replaced could silently fail to import at fire time, and
# an honest render count printed from a block that never ran is not honesty.
sys.path.insert(0, os.path.join(root, 'shared', 'scripts'))
try:
    import render_thread_live_state as r
except ImportError as e:
    raise SystemExit('ABORT Phase 3.5b — could not import render_thread_live_state from '
                     + os.path.join(root, 'shared', 'scripts') + ': ' + repr(e)
                     + '. The plugin install is incomplete; this is a REAL error to '
                     'surface, NOT a missing-feature skip.')
ws = '<workspace_root>'
# Shape-defensive read (Bug #84-followup) — flat OR wrapped entities.json.
_d = json.load(open(ws + '/_hq/data/entities.json'))
ent = _d['entities'] if isinstance(_d.get('entities'), dict) else _d
# SPEC DUALKEY1: single canonical read — entities_collection aliases the
# retired 'projects' spelling to the canonical `threads` collection.
from entities_io import entities_collection
threads = entities_collection(ent, 'projects')
refreshed, unlinked, missing_folder, errors = [], [], [], []
for t in threads:
    # FOLDERGUARD: the filter existed but covered ONE terminal status, so a
    # thread at 'resolved' rendered straight through. Widened to the codebase
    # terminal pair (deal_state.py:186, deal_signal_detector.py:210,
    # objective_state.py:621) — 'active'/'paused'/'scoping' still render.
    if t.get('status') in ('archived', 'resolved'): continue
    try:
        res = r.render_live_state(ws, t['id'])
        # Count only brains actually rendered, never merely visited. The two
        # refusals are DIFFERENT REPAIR CASES and are reported apart:
        #   'no_brain_path'     -> the record names no folder at all. Note C9 in
        #                          integrity_check SKIPS an empty folder_name, so
        #                          this line is the only place it is ever visible.
        #   'no_project_folder' -> names a folder that is not on disk (C9 sees it).
        st = res.get('status')
        if st == 'no_brain_path': unlinked.append(t['id'])
        elif st == 'no_project_folder': missing_folder.append(t['id'])
        elif res.get('rendered'): refreshed.append(t['id'])
    except Exception as e:
        errors.append((t['id'], repr(e)))  # per-thread, surfaced — never swallowed
print('refreshed', len(refreshed), 'brains;', len(unlinked), 'not linked to a folder;',
      len(missing_folder), 'folder missing; per-thread errors:', len(errors))
if unlinked: print('UNLINKED:', unlinked)
if missing_folder: print('FOLDER-MISSING:', missing_folder)
if errors: print('PER-THREAD-ERRORS:', errors)
"
```
The render is **dirty-checked** — it only rewrites a block when a thread-tagged event newer than the block's recorded `source_seq` exists, so a quiet workspace is a fast no-op. It **byte-preserves** all hand-written brain content (only the marked Live-State region changes). Record refreshed brains into `actions_taken[]`.

**Reporting rule (HONEST1).** A quiet workspace stays quiet: `unchanged` on every thread is the designed answer, not a finding, and produces no Monday-note line.

**The integrity checker owns the Monday-note line for both refusals, not this block.** `unlinked` is reported by `C9b.thread_folder_unset` and `missing_folder` by `C9.thread_folder_missing`, both already folded into the note via Phase 1's `run_checks`. The counts printed above stay in the **run record** as diagnostics — do NOT also write them into the Monday note, or the same threads get reported twice by two paths. One owner per fact. (Deal and objective threads are exempt from `C9b` by design — neither kind owns a project folder, so an `unlinked` result for one of them is the expected answer, not a finding: it stays in the run record and never reaches the note.)

`errors` is different and DOES belong here: a per-thread exception is a real failure the checker never sees, so surface its count in the run record and flag it if non-zero.

When the note renders those findings, the line must carry the fix rather than the diagnosis, and must never expose a status name, finding id or thread id to the CEO (CONTRACT Rule 28) — e.g. *"N of your projects aren't linked to their folders yet, so their status pages can't update — say `reconcile projects` and I'll match them up."* Replacing a comfortable fiction with an accurate dead end is not the goal.

### 3.5c — Regenerate the People registry (`_hq/views/PEOPLE.md`)
The people directory is a generated view that drifts when no code re-fires it (it had no renderer until v3.17.1, and stale-drifted from 95 people in the substrate down to a 69-person view). Regenerate it deterministically from the substrate every run:
```bash
python3 -c "import sys; sys.path.insert(0, 'shared/scripts'); import render_people_view as r; print(r.regenerate('<workspace_root>'))"
```
Atomic-write, idempotent (content-stable apart from the header timestamp), also updates the back-compat copy at `_hq/PEOPLE.md`. Record into `actions_taken[]` only if the active/archived counts changed from the prior PEOPLE.md header.

**ALIASES.md safety net (D7).** `_hq/views/ALIASES.md` is projected from `aliases.json` and owned by **people-crm** (regenerated on any `aliases.json` write — per `references/VIEW_GENERATION.md`). There is **no standalone aliases-view renderer** in `shared/scripts/` (verified at build time), so cleanup cannot safely regenerate it — it would have to re-implement people-crm's projection. Instead, cleanup **flags** staleness and names people-crm as the owner:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
r = ca.check_aliases_staleness('<workspace_root>')  # None if current
print(json.dumps(r))
"
```
If it returns non-None, add a Monday-note line: *"My list of name shortcuts looks out of date — say `refresh aliases` and I'll rebuild it."* Never regenerate ALIASES.md here.

### 3.5d — Regenerate the DECISION_LOG view (changed-only) (D4)

`_hq/views/DECISION_LOG.md` has a renderer (`render_decision_log.py`) and is wired into decision-write paths, but a **missed** regen (e.g. insight-generator paused, or a decision logged by a path that didn't re-fire it) then persists until the next decision is logged — which can be weeks (the forensic case: 23 days stale). cleanup is the weekly backstop. Use the **changed-only** entry point so a quiet workspace stays a true no-op write (idempotence):

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
r = ca.regenerate_decision_log_if_changed('<workspace_root>')
print(json.dumps(r))
"
```
Record into `actions_taken[]` **only when `changed` is True**. This regenerates a derived view from the substrate — it never rewrites `events.jsonl`/`entities.json`.

### 3.5d2 — Regenerate the MASTER_TRACKER view (changed-only) (D4)

`_hq/views/MASTER_TRACKER.md` is a generated projection of `entities.json` + `events.jsonl` but had **no renderer** until v4.2.0 — `VIEW_GENERATION.md` and `workspace-manager` claimed a "writer helper" regenerated it, but the only thing that ever did was the LLM hand-rendering it during end-session. When that hand-render lapsed, the tracker froze while the substrate stayed current (forensic case: M's tracker frozen from 2026-06-11 while events.jsonl ran through today). It was the only major projected view with no renderer and no cleanup backstop. v4.2.0 ships `render_master_tracker.py` and wires `regenerate` into end-session; cleanup is the weekly backstop. Use the **changed-only** entry point so a quiet workspace stays a true no-op write (idempotence):

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
r = ca.regenerate_master_tracker_if_changed('<workspace_root>')
print(json.dumps(r))
"
```
Record into `actions_taken[]` **only when `changed` is True**. The renderer reads every commitment field through `cru_match` (shape-safe) and dual-writes the canonical `_hq/views/MASTER_TRACKER.md` + back-compat `_hq/MASTER_TRACKER.md`; it never rewrites the substrate. `changed` also flips True when only the back-compat copy was missing — so this heals the `_hq/` vs `_hq/views/` path-orphan in older workspaces on the next sweep.

### 3.5d3 — Regenerate changed entity-history views (SPEC HIST1, dirty-checked)

The per-person / per-company history views (`_hq/views/people/*.md`, `_hq/views/orgs/*.md`) are deterministic compiles over events, CREATED on `go [person]` / `go [org] rollup` — cleanup keeps the existing ones fresh. Both regen hooks are seq-dirty-checked (a view is re-rendered only when an event newer than its `source_seq` marker tags that entity), so a quiet workspace is a fast no-op:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import render_person_history as rp, render_org_history as ro
ws = '<workspace_root>'
print(json.dumps({'people': rp.regenerate_changed(ws), 'orgs': ro.regenerate_changed(ws)}))
"
```

Record into `actions_taken[]` only the entities actually refreshed (both `refreshed` lists). Never hand-edit a history view — the renderers own the whole file. An archived entity's view is pruned to `_archive` on status change by the archive path, never deleted here.

### 3.5d4 — Regenerate the routing-corrections view + the repeated-redirect line (SPEC ROUTEMISS1)

`_hq/views/ROUTER_MISSES.md` is a VIEW over the routing corrections the CEO voiced in chat ("wrong skill", "that should have been…", a "no, I meant…" redirect — workspace-manager's handler writes one event per correction and re-renders on write). Cleanup is the weekly backstop for a missed regen and the ONLY place the repeated-redirect sentence is produced. Changed-only, so a quiet workspace is a true no-op:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
ws = '<workspace_root>'
r = ca.regenerate_router_misses_if_changed(ws)
print(json.dumps({'changed': r['changed'], 'total': r['total'], 'recent': r['recent'], 'repeated': r['repeated'], 'monday_line': ca.router_misses_monday_line(ws)}))
"
```

Record into `actions_taken[]` only when `changed` is True. If `monday_line` is non-null, add it VERBATIM to the Monday note's "worth a glance" tier — it is already plain English (*"Three times this month you had to redirect me to call prep; the phrases were: …"*). A null adds nothing: no line, no "routing was fine" filler (COVERQUIET1 posture). Never name the event type, never paraphrase the CEO's phrases, and never propose a description edit from it — the line is a pointer for whoever tunes this Command Room, not an instruction.

### 3.5e — Flag stale analytical views + nudge a paused insight-generator (D5)

The analytical views (`RELATIONSHIPS.md`, `TIMELINE.md`, `COMMITMENT_AGING.md`, `DORMANT.md`, `THEMES.md`) are **NOT cleanup's job to regenerate** — they're `insight-generator`'s expensive lazy synthesis (per `references/VIEW_GENERATION.md`). cleanup's job is to **flag honestly** when they've fallen behind the substrate, and to surface the real root cause (a paused insight-generator) — the forensic gap was that cleanup neither regenerated NOR actually flagged, and insight-generator wasn't firing, so nobody owned it.

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
ws = '<workspace_root>'
stale = ca.check_analytical_view_staleness(ws)            # views older than the substrate
nudge = ca.insight_generator_staleness(ws)                # has insight-generator gone quiet >14d?
print(json.dumps({'stale_views': stale, 'insight_nudge': nudge}))
"
```
- If `stale_views` is non-empty → one Monday-note line (insight-generator is the owner internally; never name it to the CEO): *"A few of your insight pages are behind — say `run insights` and I'll bring them current."* (List the page names plainly; no `_hq/` paths.)
- If `insight_nudge.stale` is True (insight-generator hasn't fired in **>14 days**, or never) → add the deeper nudge: *"I haven't run your weekly insights in a while — say `run insights` and I'll bring the patterns and pages current."* This is the honest signal that the analytical views will keep drifting until insight-generator runs.

cleanup **never** regenerates these views (D5 — duplicating the synthesis blurs ownership and is expensive). It flags, names the owner, and moves on.

### 3.5g — Rotate the event log if it's grown large (SPEC A5)

`events.jsonl` is rewritten in full on every append, so on a large workspace write cost
grows with history. Once it crosses **5 MB or 10,000 lines AND contains prior-calendar-year
events**, rotate the prior years into immutable `events-<year>.jsonl` shards (the active file
keeps the current year + a `shard_rotated` seq-continuity marker). Small workspaces never
shard. Dry-run first, then rotate if eligible. The rotation runs under the A1 writer lock and
rebuilds the A3 dedup index itself.

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import rotate_events as rot
ws = '<workspace_root>'
preview = rot.rotate(ws, now_iso='<local ISO>', dry_run=True)
res = rot.rotate(ws, now_iso='<local ISO>') if preview.get('would_archive') and not preview.get('rotated', True) is True else preview
print(json.dumps(res))
"
```

If `rotated` is True, add one reassuring Monday-note line — *"Your activity log got large, so I archived [N] older events into a yearly file — nothing lost, everything still searchable."* Omit entirely when nothing rotated (the common case). Readers are shard-transparent (the canonical loaders include shards automatically), so deep-history views keep working after a rotation.

### 3e-bis. Scheduled-task watchdog sweep (Phase 3 — W1 surface (b) + R5 missed-fire detection + R10 scheduled-output self-audit)

Cleanup is the weekly deep pass of the reliability watchdog. (The morning brief's light daily pass — `task_watchdog.brief_watchdog_line` — is RETIRED as of HEALTH1, 2026-09-07; this weekly deep pass is now the only place the finding surfaces on a schedule, alongside the on-demand `health check`.) Three layers, in order:

**1. Fired-recency + missed-fire check (W1 + R5).** Read the scheduler listing through `schedule_backend.plan_list` (cleanup runs interactively enough to afford the round trip — this is what makes it the deep pass).

**Ask availability first, of the code, never of a tool name (TRUTH1, SPEC_NIGHTM2_LANES §3 (a)).** Run `schedule_config.scheduler_availability(<this fire's tool names>)` BEFORE planning the listing. When it answers `available: False` with `reason: "scheduler_unavailable"` there is no scheduler in this fire: set `records = None` — the literal `None`, never `[]` — and `backend = None`, and do not attempt the listing. Otherwise execute `plan_list`, `normalize` it, and carry the selected backend's id alongside. `None` and `[]` are two different facts and the verdict says a different sentence for each; substituting one for the other tells the customer something untrue about their own computer. Then:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import task_watchdog as tw
ws = '<workspace_root>'
records = <the NORMALISED records from `schedule_backend.plan_list` (executed, then `normalize`), as a Python list of task dicts — or the literal None when the availability check above said no scheduler is available>
backend = <the selected backend's id ('cloud' / 'legacy'), or None in that same case>
verdict = tw.health_verdict(ws, task_records=records, backend=backend)
print(json.dumps({'vantage': verdict['vantage'], 'lines': verdict['lines'],
                  'info_lines': verdict['info_lines'], 'reports': verdict['reports'],
                  # the partition counts — info_lines is ONE flat list with no
                  # class marker, so these are the only way to tell a catch-up
                  # line from a first-run line, and the only source for the
                  # 'was this chronic?' judgement below
                  'caught_up': verdict['caught_up'],
                  'first_run_pending': verdict['first_run_pending'],
                  'on_schedule': verdict['on_schedule']}))
"
```

`task_records` gives the watchdog the scheduler's `lastRunAt` per task, which is what detects the R5 class — a task that silently skipped >=2 expected fires (`late`), a task registered but never authorized (`never_authorized`), the fired-but-wrote-nothing case (`receipt_gap`: fresh `lastRunAt`, stale substrate receipt — the render-without-write class), and the case where the scheduler's own run record says the PLATFORM declined the fire (`platform_declined`, R-M1-6: a cap or quota, not a broken computer — its line names the limit and the phrase, and asks for nothing). Fold every returned `lines` entry into the Monday note's "worth a glance" tier verbatim. With `records = None` the pass runs receipts-only — degraded but never skipped, and never silently: the vantage rule below is what says so.

**When `records` is `None`, layers 2–5 are SKIPPED BY NAME.** Every one of them reads the scheduler's own records, and there are none: layer 2 compares registered prompts, layer 3 reads which chats fired this week, layer 5 needs the registered task set. Do NOT derive a registered set from `None` (an empty set here reports every first-install chat as "missing from the schedule" — the F-40 false-outage class, on a fire that could not see the scheduler in the first place, and deriving one is how this section used to fail outright instead of reporting). The Monday note carries the verdict's `vantage['line']` and nothing else about schedules.

**Another computer still writing (SAFETY0 — RETIRE1).** When the workspace has declared its one scheduled writer and another computer is still writing scheduled chats into it, the verdict carries that one sentence itself — as one of `lines`, or inside `vantage['line']` when the scheduler is out of sight — so the fold above already puts it in the Monday note verbatim; never name the other computer, its token or its account.

**`info_lines` are not problems (M ruling 2026-08-03).** The verdict already partitions late-BUT-RECEIPTED fires into `caught_up` / `info_lines`, separately from the `late` / `receipt_gap` / `never_authorized` problems — that partition is correct and stays exactly as it is. What changes is what the note does with it: **the per-task catch-up lines do not go into the note at all.** Catch-up on wake is the designed contract for a laptop that gets closed, not an anomaly, so a task that caught up and left its receipt is a task that worked.

**Splitting `info_lines` — it is ONE flat list, so use the counts, not the wording.** The verdict builds it as the catch-up lines FIRST and the first-run lines after, with no marker on either; do not pattern-match the sentences. Take `len(caught_up)` lines off the front — those are the catch-up lines, and they are **dropped**. Everything after is a first-run line, and each of those keeps its existing one-liner treatment in the Monday note's **"worth a glance" tier** (unchanged from before this ruling). If the counts and the list length disagree, surface none of them rather than guessing which is which.

**"Chronic" is a count, not a feeling.** Chronic means `len(caught_up)` is at least half of `len(caught_up) + len(on_schedule)` — most of the week's fires arrived late — and only then does the one calm summary line below apply. A single catch-up in a healthy week earns nothing at all. Never restate a caught-up task as late, asleep, or missed.

**Vantage guard (F-40, widened by TRUTH1):** if `vantage` is non-null, this fire cannot see the scheduler that serves these chats — the Monday note carries the single `vantage['line']` sentence instead of any per-task registration claims, and layers 2–5 are skipped for this fire. Never report tasks as unregistered from a blind vantage. `vantage['check']` says which case it is (`registry_vantage`, `registry_vantage_cloud`, `scheduler_unreachable`); the line is already composed for that case, so render it verbatim and add nothing — including the `machine` field, which is evidence for this step and never text (F16).

**2. Registered-prompt drift (W4).** With the same records in hand, read the installed plugin version from `$PLUGIN_ROOT/.claude-plugin/plugin.json` and run `drift = schedule_refresh.prompt_body_drift(records, plugin_version=installed_version)` — the task ids whose registered bootloader BODY differs from the one this plugin composes today once the diagnostic stamp is normalized out of both sides (the compare Step 1.C writes on). A non-empty `drift` → one Monday-note line, `schedule_refresh.stale_prompt_notice(drift)` verbatim (CUT-PLATE, 2026-09-06 — the same sentence the update bridge and the health check say): *"Your scheduled chats are still running the setup from an older Command Room. Type `set up command room schedules` once and they'll be brought current — nothing else changes."* (One line total, not per task. `tw.check_prompt_versions` — the stamp read — is informational and never earns the line: a stamp-only difference is not drift (fix round 1, REVIEW F-1 — Step 1.C leaves the stamp alone, BRIDGESIL1 §0.2, so a stamp-keyed line would repeat every Monday of every release whose bootloader body did not change); an unstamped legacy prompt adds nothing either.)

**3. Scheduled-output self-audit (R10 — transcripts).** For each scheduled-chat thread that FIRED this week (per the watchdog reports / `lastRunAt`), read its session transcript via the session-info tools (`list_sessions` -> the scheduled-chat thread -> `read_transcript`; proven to work from scheduled sessions, 2026-07-01) and verify BOTH halves of the fire happened:
   - **rendered** — the transcript shows the widget/digest actually posted (a `show_widget` call or the digest text), and
   - **wrote** — the substrate carries the fire's receipt (`pack_run` / `sent_reconcile` / etc. — the watchdog's receipt check above already computed this; a `receipt_gap` finding IS the write-side failure).
   A fire that rendered but didn't write, or wrote but didn't render, gets one Monday-note line naming the task and the half that failed, in plain English (e.g. *"Tuesday's Inbox run showed you the summary but didn't record its work — the numbers it feeds may run a day behind."*). If the session-info tools aren't available in this fire, skip layer 3 silently (layers 1-2 still ran) — never fabricate a transcript finding.

**4. Chronic-lateness proposal (R4 consumer).** Read the late-fire telemetry and propose a better default time for any task that has been >24h late in 3 of the last 4 weeks (thresholds live in `late_fire.py` — one place):

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import detect_chronic_lateness
print(json.dumps(detect_chronic_lateness('<workspace_root>')))
"
```

Each returned `line` goes into the Monday note verbatim — it already names the task in plain English and routes the fix through `change my schedule`. PROPOSE ONLY: cleanup never moves a cron itself, and a user-customized time is never overridden (the move happens only if the CEO says so via change-schedule).

**5. Schedule-parity check (R2 — detect + report, NO config writes).** With the same scheduler records, compare the registered-task set against the merged schedule view:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import task_watchdog as tw
from event_gate import append_event
ws = '<workspace_root>'
registered = <the taskIds from the NORMALISED `schedule_backend.plan_list` records, as a Python set>
parity = tw.check_schedule_parity(ws, registered)
append_event(f'{ws}/_hq/data/events.jsonl', {
    'type': 'schedule_parity_checked',
    'source_skill': 'cleanup',
    'data': {k: len(v) for k, v in parity.items()},
}, holder='cleanup')
print(json.dumps(parity))
"
```

- **`ghost_first_install`** (a first-install task enabled in the config/defaults but missing from the registered set) → real breakage; one Monday-note line: *"Your [Display Name] task is missing from the schedule — say 'set up command room schedules' to restore it."* (The watchdog layer above usually catches this too; don't double-report the same task.)
- **`ghost_later_add`** (a task in `later_add_task_ids()` not registered — read the set, never a list typed here) → EXPECTED, say nothing. A RETIRED task (`schedule_config.RETIRED_TASKS`) is the same silence for a different reason: it is gone, not pending, so never say it is missing and never propose adding it — the R3 proposal step owns that nudge. **Retired ids cannot reach either bucket anyway** (retirement takes the row out of `DEFAULT_SCHEDULES`, which is what the parity view iterates), and TASKRET1 is why that is worth knowing: `commitment-triage` and `balance` were named in this very sentence as later-adds until 2026-08-17, so a hand-typed roster here goes stale on exactly the releases that matter most.
- **`orphan_overrides`** (a `schedule_config` entry for a taskId that exists in neither DEFAULT_SCHEDULES nor the registered set — e.g. a legacy `cr-*` key) → one Monday-note line: *"An old schedule setting from a previous version is lingering — harmless, but say 'change my schedule' and 'back to defaults' if you ever want a clean slate."* FLAG ONLY: the only heal for an orphan override is a removal, and cleanup never removes; under sparse-config semantics there is no safe ADDITIVE heal (densifying would destroy the customized-cron signal), so the heal direction stays flag-only and `schedule_config_healed` remains registered-but-unwritten.
- The audit event's counts are what make the weekly check auditable — never narrate the event name to the CEO.

**6. Later-add task proposal (R3 — propose, NEVER auto-register).** When the parity check reported a `ghost_later_add` set (or on any healthy week), run the readiness check:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from schedule_proposals import propose_later_add_tasks, log_proposal
ws = '<workspace_root>'
registered = <the taskIds from the NORMALISED `schedule_backend.plan_list` records, as a Python set>
proposals = propose_later_add_tasks(ws, registered)
for prop in proposals:
    log_proposal(ws, prop['task'])   # the 6-week suppression record
print(json.dumps(proposals))
"
```

Thresholds live in ONE table (`schedule_proposals.PROPOSAL_THRESHOLDS`): the staff-meeting (LB1 R4 — it absorbed the standalone relationship-moves proposal slot; existing relationship-moves registrations are untouched) qualifies on ≥8 prospect+client orgs AND ≥14 days of accumulated dormancy signal, OR ≥3 open Living Brain proposals waiting on the user; dormant-customer-scan (≥5 clients) is offered only as the lighter alternative when the staff meeting doesn't land — never both in one round. Surface each returned `line` verbatim in the Monday note's "worth a glance" tier, citing the real counts the helper already baked in. The add itself flows through the EXISTING paths (say `add staff meeting` → change-schedule / registration Phase 6 add) — cleanup registers NOTHING. A proposal that was surfaced is automatically suppressed for 6 weeks; an empty result adds no line.

Enforcement note (the #98 lesson, generalized): every check above binds to artifacts — substrate receipts, scheduler records, transcripts — never to what a fire *narrated*. The watchdog READS receipts; it does not trust narration.

### 3f. Deliverable voice/privacy sweep — the GATE2 backstop (SPEC GATE2 D3)

This is the load-bearing layer of GATE2's enforcement-by-detection. The save-time voice + leak gates only run when a deliverable routes through `brief_writer.make_brief` (or, for premium HTML, `premium_html.make_premium_brief`); live testing proved the LLM routinely hand-rolls a `.docx` via the generic docx skill (0 `gate_ran`, every gate bypassed), and the same bypass applies to a hand-rolled or later-edited `.html` page. So instead of trusting the route, cleanup **reads the documents that were actually produced this week** — Word docs, deliverable-shaped markdown, and `.html`/`.htm` renders — and flags any carrying a voice tell or a privacy/substrate leak, regardless of how they were made. The scanner opens the file itself, so a hand-rolled doc is caught exactly like a `make_brief` one.

**READ + FLAG ONLY — never deletes, moves, or edits a user's file** (client safety, same posture as orphan folders in 3d). "Quarantine" here means surface loudly, not relocate. The only writes are CR-owned telemetry (a `gate_ran` event + a findings record under `_hq/.system/gate2_findings/`), and they can never block.

```bash
python3 -c "
import sys, json, time; sys.path.insert(0, 'shared/scripts')
import deliverable_sweep as ds
ws = '<workspace_root>'
since = time.time() - 7*86400          # only docs produced in the last 7 days
res = ds.sweep_workspace(ws, since_ts=since, emit=True, source='cleanup_sweep')
bypass = ds.detect_gate_bypass(ws, since_ts=since)   # D5 gate_ran join (cheap complement)
summary = ds.summarize_for_user(res)
print(json.dumps({
    'scanned': res['scanned'],
    'violations': res['violation_count'],
    'warns': res['warn_count'],
    'errors': res['error_count'],
    'suspected_bypass': bypass['suspected_bypass'],
    'summary': summary,
}))
"
```

Fold the result into Beat 1 (see below):
- If `violations > 0` (or `errors > 0`): surface the `summary` string verbatim under the "worth a glance" tier — it names each doc by filename and the offending language in plain English (no `_hq/` paths, no token jargon). This is the "this document didn't pass the quality gate" flag, reaching the CEO before they forward it.
- If `suspected_bypass > 0` AND there were no content violations: a softer line — *"A few documents were produced this week without going through the quality check — worth a glance to confirm they sound like you."* (The content sweep already covers the ones still on disk; this catches deliverables that left the workspace.)
- Clean sweep (all zero): add nothing.

> **Honest framing (SPEC GATE2 D7, updated OUTGATE1 2026-09-08).** This sweep is why the product claim is "Command Room **detects and flags** voice/privacy violations in what it produces, before they leave your hands" — not "bad output can't be produced." An LLM with code access can always hand-roll a doc; reading the produced file is what makes the violation catchable. The weekly cadence here and the on-demand `check-deliverables` fire are now the WHOLE detection story — there is no same-turn Stop hook any more. `hooks/hooks.json` and `gate2_turn_sweep.py` are DELETED (M ruled 2026-09-07 that Cowork never runs plugin hooks; confirmed the hard way by `BUG_2026-09-07_turn-hook-silent-and-gates-disagree` — the hook silently stopped emitting for two weeks and disagreed with this same weekly sweep's `docx` surface while it ran). The `writer_gate` line above (Phase 3d-quinquies) is what now makes a save-time gate's own silence detectable, on the maintenance run, in place of the hook.

### 3g. Voice draft-snapshot pruning (B1)

`_hq/voice/draft-snapshots.jsonl` holds drafted email bodies kept only long enough to diff against the sent version (voice calibration). Prune rows that are **already matched** (a correction row for the same `draft_event_seq` exists in any `_hq/voice/corrections-*.jsonl`) OR **older than 90 days**. READ + rewrite the snapshots file only — never touch corrections logs or any user deliverable. Bodies are workspace-private (same class as transcripts); pruning is mandatory so they don't accumulate. If the file is absent, no-op.

### 3h. source_ref dedup index verify (A3)

The dedup index (`_hq/data/.source_refs.idx`) is a cache over events.jsonl that keeps duplicate captures out regardless of age. It self-maintains on every append, but manual events.jsonl surgery (corruption recovery, quarantine release) can leave it divergent. Run `python3 shared/scripts/source_ref_index.py verify <workspace_root>`; on `MISMATCH`, run `rebuild` and add one line to the Monday note ("tidied up one of my behind-the-scenes indexes — nothing changed in your data"). Run rebuild AFTER any corruption-recovery path in this cleanup that touched events.jsonl. Also move any cloud-sync conflict copies matching `.source_refs*.idx` that aren't the canonical name into `_archive/dedup-index/` (archived, never deleted). If the index matches, say nothing.

### 3i. Long-unconfirmed commitment sweep (v4.6.1 W4b — PROPOSE only)

Captures that have sat unconfirmed (pending_review / no owner / suspected duplicate) for 30+ days almost certainly resolved outside the system or were never real — the weekly note proposes Drop; the drop itself is ALWAYS a manual click on the triage surface, never something cleanup does. Read-only here:

```python
import sys; sys.path.insert(0, "shared/scripts")
from cru_match import load_open_commitments
from confirm_flow import select_unconfirmed_escalation

opens = load_open_commitments("<WORKSPACE>/_hq/data/events.jsonl")
stale_unconfirmed = select_unconfirmed_escalation(opens, "<now ISO>")["propose_drop"]
```

A non-empty result adds ONE line to the Monday note's Beat 1 (see below). Zero → nothing. No events written, no receipts beyond the standard cleanup_run, and this never touches the scheduled-task watchdog pass (3e-bis) or its note lines.

### 3j. Living Brain proposal expiry sweep + card health (SPEC LB1)

The anti-fatigue contract's back half: an ignored proposal expires SILENTLY at its TTL (default 14 days) — logged, never nagged — and the expiry count is visible here so queue rot reaches the CEO's dogfood without a nag. Two calls, both through the canonical module:

```python
import sys; sys.path.insert(0, "shared/scripts")
from brain_proposals import expire_stale, card_health_counts

swept = expire_stale("<WORKSPACE>")          # appends brain_proposal_expired tombstones (bounded write, see Writer Contract)
health = card_health_counts("<WORKSPACE>")   # {"open": N, "expired_in_window": M, "resting_auto": R} — 30-day window; R is ALWAYS 0 on a healthy workspace (LB2 auto lifecycle contract)
```

The sweep is the ONLY expiry writer (surfaces already exclude TTL-past items from render, so a missed Sunday never shows stale rows — the tombstone just makes the ledger explicit). Feed `health` into the Beat 1 card-health line below. Never narrate proposal ids or event types. **LB2:** `health["resting_auto"]` counts open auto-tier proposals — by the auto lifecycle contract that number is ALWAYS 0; a non-zero value is a detector bug (an automation proposed an auto change and never applied/resolved it in the same run). When non-zero, add ONE worth-a-glance line — *"One of my background automations left something half-finished — I've flagged it for the operator."* — and treat it as a `report bug` item; never render the row itself.

### 3k. Config-drift re-offer pass (SPEC LB2 §3b — FIRST_RUN_PROTOCOL "Override-drift", mechanized)

The first-run contract's drift clause finally has code: a knob configured **>6 months ago** that has collected **≥5 tagged contradicting signals** since (corrections rows fighting a configured sign-off; per-fire overrides of a configured default) gets ONE re-offer proposal on the Living Brain rail — `kind: config_drift`, **staff meeting only** (a config nudge is never urgent; it never reaches the daily card or the brief). One call, weekly:

```python
import sys; sys.path.insert(0, "shared/scripts")
from config_drift_detector import run_drift_detector

drift = run_drift_detector("<WORKSPACE>")   # {"candidates": N, "proposed": K, "suppressed": S}
```

**Cleanup stays READ-ONLY on prefs** — the helper proposes, never writes `skill_config` (Writer Contract rider; byte-checked by test). Once-per-knob discipline is `propose()`'s own machinery: an open row dedups, a dismissal takes the standard 60-day cooldown, snooze is the shared 7d. On confirm (at the staff meeting, via apply-choices), a re-offer note lands for the next coach session to re-offer THAT KNOB — the tune flow remains the only config writer. No Monday-note line for this pass — the proposal IS the surface. Never narrate detector internals or signal counts beyond what the row's own text says.

### 3l. Objective-link proposal pass (SPEC OBJ2 §1B — provisional classifications onto the Staff Meeting rail)

A captured item whose classification envelope **provisionally** targets an open standing objective (confidence below the auto-attach band, or attribution flagged pending review) gets ONE link proposal on the Living Brain rail — `kind: objective_link`, **staff meeting only** (adjudication is never urgent; it never reaches the daily card or the brief). One call, weekly:

```python
import sys; sys.path.insert(0, "shared/scripts")
from objective_link_detector import run_objective_link_detector

links = run_objective_link_detector("<WORKSPACE>")   # {"candidates": N, "proposed": K, "suppressed": S}
```

The detector consumes the envelope stamps that already exist — it never hooks a capture pipeline or re-reads content, and it reads events **org-scoped only** (masked/personal-lane items can never drive a row). Once-per-link discipline is `propose()`'s own machinery: an open row dedups, a dismissal takes the standard 60-day cooldown, snooze is the shared 7d; a link the CEO already confirmed never re-lists. No Monday-note line for this pass — the proposal IS the surface. Never narrate detector internals or confidence numbers beyond what the row's own text says.

### 3z. Phase-ordering reconcile (FOLDERGUARD §2.5 — the meta-guard, runs LAST)

Every mutating phase has now run. Re-run the checker and compare against the Phase 3a snapshot: **a finding must not disappear unless something claims credit for it.**

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import integrity_check as ic, phase_order_guard as pog
from pathlib import Path
before = json.loads(Path('<session_dir>/phase3a_snapshot.json').read_text(encoding='utf-8'))
after = pog.snapshot(ic.run_checks(Path('<workspace_root>')))
actions = <actions_taken>   # the list accrued across this fire
unexplained = pog.reconcile(before, after, actions)
print(pog.format_report(unexplained) or 'PHASE-ORDER GUARD — clean')
"
```

This is the assertion that would have caught the phantom-folder bug in week one. Phase 3a raised `C9.thread_folder_missing`; Phases 3.5a/3.5b then created exactly those folders later in the *same* fire, so the next scan read `C9 = 0` and reported clean — the run concealed its own damage, and the fabricated folders only ever resurfaced as `C10.orphan_folder`, which Phase 3d merely flags. No per-phase assertion can see that; it is only visible across the fire.

**Surface, never auto-fix.** Anything reported here is either a silent self-heal worth naming or a phase overwriting a finding it should have surfaced — both belong in the run record as a plain-English line ("one thing I flagged earlier quietly went away this week — worth a look"), and neither is safe for cleanup to act on unattended. Fold the count into `items_flagged_for_user[]`. A clean result says nothing to the CEO.

## Phase 4: Monday-Morning Report — the scorecard handshake (no scores)

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "Your event log had some write contention this week (3 waits, 1 timeout) — the writer lock held."
- GOOD: "A few things tried to save to your activity log at the same moment this week — everything saved fine, nothing was lost."

**Three short beats: what I tidied → what I handled for you → what's waiting / what you missed.** Keep it TIGHT — this is a handshake, not a recap (Friday Wrap owns the business recap; the monthly operator-report owns the deep value proof). It's the silent proof that Command Room earned its keep this week.

### Beat 1 — What I tidied (needs-eyes tiers)

Lead with what's done and what (if anything) needs eyes. Three tiers, mapped from an internal assessment — the tier mapping carries over from the old audit, **the score itself does not and is never surfaced.**

- **Clean tier (default):** one line. `"Tidied up this weekend — filed away [N] old briefings and [M] long-finished commitments. Nothing needs your eyes."`
- **A few things tier:** short, plain, forward-action.
  ```
  Tidied up this weekend. A couple of things worth a glance:
  • Your Acme notes haven't been touched in 6 weeks — want to mark the project paused?
  • Two commitments to Sam are aging past 30 days.
  Nothing else needed cleaning.
  ```
- **Backlog tier:** a 3-item prioritized list ("this week's three"), plain English, no scores, no alarm language, forward-action lead.

**Beats the CLEAN1 scan feeds into Beat 1 (include only the ones with real findings — omit zeros):**
- **Orphan folders found** (Phase 1.0 `orphan_folder`): *"I noticed [N] folders that aren't tracked yet — [names]. Want me to register or archive them?"* FLAG only; never moved.
- **Session-notes backfilled** (Phase 3c, D3): *"[N] projects were missing a notes file — I started one for each so they stay current."* (Only counts files actually created; the helper never overwrites existing notes.)
- **Repeated redirects** (Phase 3.5d4, ROUTEMISS1): when `monday_line` is non-null, that ONE sentence verbatim — it names the skill in words and quotes the CEO's own phrases. Null adds nothing.
- **Stale insight views** (Phase 3.5e, D5): the single line — *"A few of your insight pages are behind — say `run insights` and I'll bring them current."* Add the >14-day insight-generator nudge when `insight_nudge.stale` is True.
- **Long-unconfirmed items** (Phase 3i, v4.6.1 W4b): when `stale_unconfirmed` is non-empty, ONE line — *"[N] captured to-dos have sat unconfirmed for over a month — say `triage my commitments` and I'll queue them up to drop or keep."* PROPOSE only (the drop is a click on the triage surface, never automatic); omit on zero. Keep this line out of the watchdog cluster below — it's a substrate-hygiene item, not a schedule finding.
- **Living Brain card health** (Phase 3j, LB1): when `health["open"] > 0` or `health["expired_in_window"] > 0`, ONE line — *"[N] suggestions are waiting on your yes/no — say `staff meeting` to run through them. [M] older ones expired quietly without an answer this month."* (Drop either half at zero; drop the line when both are zero.) This is the queue-rot visibility line — if the expired half keeps growing, the card cadence or the detectors need tuning, and this line is the evidence. Substrate-hygiene item — keep it out of the watchdog cluster.
- **Session notes rolled over** (Phase 2 item 4, Rule 1): fold the count of `rolled_over` records into the "tidied up" line — *"…filed [N] projects' older session notes into their archive so the live notes stay short."* Never name the archive filename, the line counts, or the safety copy unless the CEO asks. `skipped` records are silent.
- **Session notes that couldn't be rolled automatically** (the `aborted` records): decide from **THIS run's records only** — the fire has no memory of last week's, so never condition the line on how many weeks a file has been failing. When this run returned one or more `aborted` records, add exactly ONE calm line carrying the count and the way to finish the job: *"[N] projects keep their session notes in a shape that's too custom for me to reorganize on my own — say 'roll over my session notes' and I'll do them with you."* At zero aborts, omit the line entirely. Never list the files, never name the reason code, never a path, and never alarm framing — nothing was lost and nothing is broken; those files are simply the ones that need a person in the room.
- **Lock files archived** (Phase 2 Rule 9, D6): fold the count into the "tidied up" line — *"…tidied away [N] leftover lock files."* Plain English; never say "lock.stale" or surface a path. (They're moved to `_archive/`, never deleted — but don't burden the CEO with that detail unless asked.)
- **Delete permission** (Phase 2 Rule 11a, DEL1): when `delete_grant.monday_note_line()` returns a sentence, add it VERBATIM to the "worth a glance" tier — it is already plain English and already free of paths, versions and the tool's name. `None` adds nothing: a week where nothing was asked says nothing about asking (COVERQUIET1 posture). Never pair it with an apology and never re-ask in the note.
- **Files set aside instead of deleted** (Phase 2 Rule 11a, DEL1 fix round 1 M-2): when `delete_grant.asides_note_line()` returns a sentence, add it VERBATIM to the "worth a glance" tier. It is a COUNT of files the mount would not let me delete, which I renamed out of the way instead — nothing was removed and nothing was lost, and the line says exactly that. `None` (the common case, and always the case on a seat where deletes work) adds nothing. Never name a file, a folder or a path, and never offer to clear them: collecting them is a decision nobody has made.
- **Read-alarm sidecars pruned** (Phase 2 Rule 11, LB2 D5): ONE line, only when the count is non-zero — fold into the "tidied up" line: *"…cleared [N] old system health notes."* Never say "sidecar", "readalarm", or surface a path; omit entirely at zero (the common case).
- **Deliverable voice/privacy flags** (Phase 3f, GATE2): when the sweep flagged docs, surface its plain-English `summary` verbatim — *"[N] documents produced recently didn't pass the quality gate — worth a glance before any go out: • [filename] — language that doesn't sound like you ('leverage')…"*. Filenames only, never `_hq/` paths or token jargon. When only `suspected_bypass` is non-zero, use the softer "produced without the quality check" line. Omit entirely on a clean sweep.
- **Scheduled-task watchdog findings** (Phase 3e-bis, W1/R5/R10 + R3 truth rules): surface each returned `lines` entry verbatim under the "worth a glance" tier — dead task, never-authorized task, folder rename, prompt drift, render-without-write. These lead the tier when present (a dead schedule starves every other surface). If the vantage guard fired (F-40), its single line replaces ALL per-task schedule claims. Omit entirely when the watchdog returns nothing (the common case). A task named in any of these lines is never simultaneously described as running normally elsewhere in the note.
- **Caught-up fires are NOT a finding** (the `info_lines` half — M ruling 2026-08-03: catch-up on wake IS the model): a client's computer is a laptop that gets closed, and MAINT1's receipt-driven due-ness plus CATCHUP1's periods are designed so a missed slot self-heals at the next wake. **A fire that ran late and left its receipt did its job.** Do not list the per-task catch-up lines in the note, do not count them into anything that reads as "needs attention", and never reach for "the machine appears to be asleep" framing — to a CEO that reads as breakage when the system is working exactly as designed. When catch-ups were CHRONIC this week (most of the week's fires, not one or two), allow at most ONE calm informational line — *"Your scheduled work ran when the computer woke up this week rather than at its set times — normal on a laptop, and everything ran."* — and nothing more. The `first_run_pending` info lines keep their existing one-liner treatment.
- **The alarm is reserved for fires that never caught up.** A slot with no receipt after the machine has demonstrably been awake past it is a real gap, and those keep their visibility exactly as it is today: the watchdog's `late`, `receipt_gap` and `never_authorized` classes stay in the `lines` tier above, worded as they are. Nothing about the detection changes here — the checks, thresholds and classes are untouched; this is only which findings are allowed to sound like a problem.
- **Events-lock contention** (Phase 2 Rule 10, A1): include ONLY when there were waits or timeouts this week — *"A few things tried to save to your activity log at the same moment this week — everything saved fine, nothing was lost."* Omit the line entirely on a quiet week (the common case). Never surface file paths, "lock_stats", wait/timeout counts, or lock vocabulary; the point is reassurance that it was handled, not a metric dump. The counters reset after this report.

**Set-aside audit line (W4c — own paragraph, non-zero weeks only).** Read `capture_gate.observed_counts(workspace_root, since_ts=<7 days ago ISO>)` and, when `observed > 0`, add exactly one sentence to Beat 1: *"I also set aside [N] items from meetings and chats that looked like other people's to-dos — ask me to show them if you want a look."* Never a per-item list, never the words "observed" or "tier"; omit entirely at zero. (Visible rejects are what make the capture filter trustworthy.) HYG1: `observed` counts LIVE items only — 30-day-expired set-asides report under the separate `expired` field and never inflate this sentence; nothing is deleted, they just age out of the surfaces.

### Beat 2 — What I handled for you (the value, this week)

A short concrete line of what Command Room delivered this week — the "we did our job" proof. Compute from `events.jsonl` over the last 7 days (the same idea as `operator-report`'s "delivered without being asked," just weekly and shorter). **Specifics, never a score:** *"This week: 12 morning briefs, 8 meeting preps, 14 drafts in your voice, 6 commitments captured from meetings, 2 customers flagged going quiet."* Only categories with real counts; omit zeros. One line, two at most.

### Beat 3 — Where things stand with you (adaptive — never a scold)

Branch on how engaged the CEO was this week (their actions vs their own trailing ~4-week baseline — on-demand commands fired, scheduled items acted on, projects opened, commitments closed):

- **Engaged week → surface the plate (a mirror, not a grade).** *"Waiting on you: 4 commitments aged past their date, 3 drafts I prepped you haven't sent, Northstar's gone quiet for 18 days."* Answers "did you do yours?" by showing what's outstanding — service, not judgment.
- **Light week (notably below their baseline) → the activation nudge.** Show the value left on the table, each paired with the exact words to capture it next time — FOMO + a free lesson, NEVER "you didn't use me":
  - *"You had 6 meetings on your calendar — I only prepped 1. Say `prep me for my [meeting]` and I'll have a brief ready 5 minutes before."*
  - *"~15 emails went out; I drafted 0. Try `draft a reply to [name]` — I'll match your voice."*
  - *"3 Granola transcripts landed I never processed. `process the call` pulls the action items in 60 seconds."*
  - *"You haven't opened [project] in 3 weeks. `go [project]` brings you current instantly."*
  Pick the **1–3 highest-payoff** missed opportunities; never a wall of "you should have."

**Don't-nag guardrail (mandatory).** The nudge must not become a weekly guilt drip — that churns a light-by-choice CEO faster, the opposite of the intent. **Rotate** the examples week to week (never the same nudge verbatim); if the CEO ignores it ~3 weeks running, **back off** to just Beat 2 (a good chief of staff reads the room, it doesn't hector). Tone test every line: would a trusted human chief of staff say this to their CEO? If it reads as grading or guilt, rewrite it as service.

**Forbidden in the user-facing surface** (per CONTRACT Rule 4): vendor self-congratulation ("look how much we did!"); any line that grades the CEO's effort or guilt-trips low usage (Beat 3 surfaces value + opportunity, never judgment); score numbers; ALL-CAPS headers; 🟢/🟡/🔴 grade emoji; internal mechanism names (`classifier health`, `tail_hash`, `cleanup_run event`, `org tree`, `boundary violation`); raw `_hq/` paths; the words FAIL/ERROR/CRITICAL/VIOLATION; self-narration ("Phase 1 scanning…"). When the self-heal fixed corruption, say it plainly: "I noticed your activity log looked a little off and tidied it up — nothing lost." Never "events.jsonl tail_hash mismatch."

## Phase 5: Save the Report

Generate a `.docx` at `[WORKSPACE_ROOT]/_hq/cleanup-reports/[YYYY-MM-DD]-cleanup.docx` via `brief_writer.py` **only** when there's something substantive to surface (A-few-things / Backlog tiers). Clean weeks: no doc, just the one-liner. The .docx body follows the same non-technical voice (no scores, no alarm language, forward-action framing). Surface it as the canonical H2 deliverable link at the bottom of the chat turn per CONTRACT Rule 3. Create `cleanup-reports/`, `briefings/`, and `summaries/` under `_hq/` if missing.

- **NEVER hand-roll the cleanup report** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or leaking report (the v3.20.0 failure mode) — and Phase 3d above is the sweep that catches everyone else's hand-rolled docs, so this skill hand-rolling its own is the exact failure it exists to detect.
- **NEVER create, render, copy, upload, or update the report — or any part, derivative, or restatement of it ("what you should look at", "a summary") — through Claude Docs (the built-in docs / artifact page), Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `_hq/cleanup-reports/` (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "so I can read it on Monday", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the cleanup report in a Google Doc" is a request this gate refuses, not an override. A connector copy also lands outside `_hq/`, where next week's sweep cannot see it — hand back the `.docx` link instead.

## Reliability

Runs as a job inside the `maintenance` scheduled task (Sunday evening slot — CEO reviews Monday AM; a missed Sunday self-heals at the next fire) and implements `shared/RELIABILITY.md`. Point-in-time snapshot (no missed-fire catch-up; runs at next opportunity), runs normally during OOO, 15s per-connector / 60s aggregate timeout budget.

**This skill is NOT a backup snapshotter.** It takes no daily data-file snapshots and rotates none — the safety net for the substrate is `atomic_write`, on every write, not a scheduled copy:

- Every canonical write goes through `shared/scripts/atomic_write.py` — temp sibling + fsync + atomic rename, so no reader (Cowork, another machine, a concurrent skill) ever sees a torn file, plus a cross-process lock on `entities.json` / `aliases.json`.
- `atomic_write_json_locked` re-reads the file after writing it and, if the result does not parse, **best-effort restores from the newest existing backup in the sibling `_hq/data/_backups/` folder** and raises so the calling skill knows the write did not take. That restore is only as good as what is in that folder — nothing writes it on a schedule; it accumulates from one-off migrations (`migrate_persons_v3_13_0.py`) and the end-session tracker rolling copy. Where a restore is impossible the write simply fails loudly, which is the honest outcome.
- Corrupted `events.jsonl` is **never restored over** — it is append-only history. It heals via the Phase 3b recurring self-heal, which quarantines just the malformed lines to `_hq/.system/quarantine/` (saved, never deleted), rewrites the file without them atomically, and appends a `corruption_recovery` event.

Cleanup's role here is the Phase 3b self-heal and reporting what it healed — not snapshotting.

## The activity log is append-only (MANDATORY — CONTRACT Rule 31)

This skill touches `_hq/data`. **The activity log is never rewritten by hand.**
`events.jsonl` and its yearly shards are only ever ADDED to, through the
writers (`event_gate.append_event` / `atomic_write.atomic_append_jsonl`). No
step here, and no turn this skill runs in, may edit, truncate, reorder, delete
lines from, back up and rewrite, or restore that file — and may never instruct
anyone else to.

- A duplicate or malformed line is **quarantined through the cleanup skill's
  existing path**, never deleted (`recover_corruption.py` for malformed lines,
  `seq_health.py --mark` for a duplicate entry number).
- Correcting writes this skill made means **appending a reversal through
  `brain_undo.undo_batch`** with the batch ref the run advertised — a receipt
  and a real `undo`. `undo` after a re-run means exactly that batch, or the
  words "nothing to reverse"; never an improvised drop, an invented supersede,
  or a hand-edited file.
- If you believe the file itself must change, **STOP and say so in plain
  words.** Do not do it, and do not offer to.

## Gotchas

- **Phantom projects** (tracker says active, no folder) → Phase 3c auto-fixes this one: remove the stale pointer and note it in `actions_taken[]`; don't invent a folder.
- **Orphan folders** (folder, no tracker entry) → **FLAG ONLY** (client safety, D1 — same rule as Phase 3d): surface in the Monday note with the "register or archive?" prompt and let the CEO decide. Never move, archive, or delete without the CEO's explicit instruction.
- **Session-notes path** lives at `[WORKSPACE_ROOT]/[Project Name]/SESSION_NOTES_[NAME].md`, not a subfolder.
- **Inbox** is a section in MASTER_TRACKER.md, not a folder.
- **Content-accuracy false positive** — a paused/steady-state project isn't "drifted"; respect the stage field.
- **Duplicate seqs are NOT corruption the self-heal touches** — they're valid JSON; only flagged, never auto-rewritten (would break the hash chain).
- **Staleness thresholds** — if MASTER_TRACKER has no "Staleness Rules" section, use defaults and note it.

## What It Doesn't Do

- Does not produce a narrative recap — `weekly-recap` owns the 7-day narrative.
- Does not surface a score — workspace health is binary (tidy / needs your eyes), never graded.
- Does not generate a dashboard — retired; the Monday note has the same information in plain English.
- Does not ask "want me to fix these?" — it does the safe fixes and surfaces only judgment calls.
- Does not rewrite append-only history — corruption is quarantined, duplicate seqs are flagged for a deliberate converter, old `audit_run` events are read but never mutated.
- Does not run destructive repairs silently — only the bounded safe set in Phases 2 & 3c; everything ambiguous is flagged.
## Narration leak scan (LEAK2 — MANDATORY on every composed line)

Widget bodies are scanned inside `widget_transport.render_and_persist`; the PROSE this skill composes around them is not, unless this step runs. Before posting any sentence you composed — an ack, a header, a summary, a pointer, an option, a `Sources:` line, a "why" line — run `validate_chat_output(<the text>)` from `chat_output_renderer.py` (`shared/scripts/`). It raises `LeakDetectedError` on a raw id (`person_NNN`, `project_NNN`, `org_NNN`, a `cmt_` / `bp_` / `pcand:` wire id, a bare mail-message id or UUID), an event or field name, a file name, a folder path, a script name, a spec or lane code, test-battery talk, or a score. ABORT the post and rewrite the sentence with the entity's name (`narration_names.humanize(text, narration_names.name_index(<WORKSPACE>))` is the one substitution; `narration_names.safe_name` is the one fallback when a record has no name — an honest gap, never the id). NEVER catch the error and post anyway. Text relayed byte-exact from a driver or the transport is already scanned and is not re-composed.

**Where it bites here.** The weekly Monday note relays `render_router_misses.monday_note_line`, which is already plain; anything you compose AROUND it — what the routing page is, why it exists, what a cleanup pass would do — is yours and is scanned. Never name the view's file, the script that regenerates it, or the folder either lives in.

