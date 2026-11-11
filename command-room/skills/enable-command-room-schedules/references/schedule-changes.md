# Schedule changes an update ships with (cr1#970)

**Who runs this file, and when.** The owner's ruling of 2026-11-09 (cr1#970): scheduled chats are created, changed, switched off or removed by exactly two skills, `enable-command-room-schedules` (*set up command room schedules*) and `cockpit-setup` (*set up my hub* / *rebuild my hub*). The update bridge touches none of them; it says at most one line naming *set up command room schedules* while this file's work is waiting (`schedule_handoff.pending_line`). Both owners run this file **every time they run**, start to finish, after their own registration:

- `enable-command-room-schedules`, Phase 5.95: after Phase 5.9, before Phase 6, on every run that got past Step 0.0 and Step 1.B2 (a seat that cannot have schedules, or whose chats another computer or another account holds, stopped there and never reaches this file).
- `cockpit-setup`, Step 2b: after Step 2's six plans were executed and landed, before Step 3, on every run that got past Step 2's `ok: false` line.

The work here is idempotent: on a seat with nothing to change every step below plans nothing, says nothing and writes nothing but the last step's one record. `<this skill>` below is the name of the skill running the file, and it is the `source_skill` of every record written here.

**What is NOT here, so there is one writer for each thing.** Morning Brief and Maintenance are created by the owner's own registration (schedule setup's Phase 3 and Step 1.D; the Hub's Step 2, plans 5 and 6), never by a step of this file, and their prompts are refreshed by it, except on a Hub run, where step 6 compares them because Hub Step 2 cannot: the reinstall plan below is run for its removals only. The scheduled writer is declared by schedule setup's Step 1.B2, the prompt compare and refresh of every registered chat is schedule setup's Step 1.C (step 6 below brings the same compare, Morning Brief and Maintenance included, and the cron re-anchor to a Hub run), and the superseded silent tasks are switched off by schedule setup's Step 1.D and Phase 5.9 (step 5 repeats it, so a Hub run applies it too).

## Step 1: Read the registered set once (FS-16 readback, BRIDGE3)

Read the scheduled-task listing ONCE, every page, AFTER the owner's own creates and updates landed, through the seam: execute the plan `schedule_backend.plan_list()` returns and normalise it with the stored trigger map (`backend.normalize(raw, trigger_map=TRIGGER_MAP)`; `TRIGGER_MAP` from `schedule_backend:read_trigger_map`, read where the folder is). Keep the NORMALISED records as `RECORDS`. Schedule setup reuses the listing Phase 5.9 read when nothing was written to the scheduler since; otherwise read it here.

- **A record that carries a `path` and no `prompt` (the scheduled-tasks listing: it returns a `path` to the task's SKILL.md, never the prompt text): Read the file at that `path` and set the record's `prompt` to its text, then drop `path` from the record.**
- When the readback itself fails (the tool errors), keep no `RECORDS` and skip steps 2 to 6: write nothing, say nothing, and do not write step 7's record, so the update's one line stays on and the next run tries again. **Never substitute an empty list for a failed readback:** `[]` means "I looked, and nothing is registered", which licenses acts a failed read does not.

## Step 2: Remove the previous version's chats (the reinstall's removals; SHELVE1, cr1#618, cr1#942, cr1#914)

The cockpit page (cr1#608, #619) replaced the daily chats, so only Morning Brief and Maintenance stay scheduled from the previous version; Inbox, End of Day, Friday Wrap, Waiting On and My Plate are shelved (`schedule_config.SHELVED_TASK_IDS`; their typed phrases still run each surface). On the cloud scheduler a shelved or retired chat of the previous version that is still on this seat is removed here. Write `{"registered": RECORDS}` as one JSON object into THIS SESSION'S OWN scratch (never under the workspace) and render ONE read through the door, pasting what it prints:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"reinstall": true, "tools": [<the tool ids visible in THIS session>], "workspace_root": "<WS>", "plugin_version": "<the installed plugin version>", "tz_name": "<the workspace zone>", "writer_pair": ["<the CR_WRITER_ID this run's rendered lines carry>", "<the CR_WRITER_DERIVATION they carry>"]}, "args_file": "<your scratch>/cr_schedule_changes_registered.json", "name": "schedule_backend:bridge_plan"}'
```

Render it with `workspace_access.py plan run_helper --json '…'`. The folder's path is not an argument: it crosses on the line as `CR_DEVICE_WORKSPACE`. Read `result`:

- `ok: false` (the older desktop app's scheduler, which has no delete; no scheduler; no account) → nothing is removed here; say nothing and go on to step 3.
- **`removals`**: execute each through the scheduler's delete call (`tool`, `args`), one at a time, and keep two lists of task ids: `REMOVED_OK` (the delete succeeded) and `REMOVED_FAILED` (it failed). They are shelved or retired chats only, never Morning Brief or Maintenance. The plan has already left out the cockpit's own tasks (`cockpit-…`), any registration at another folder path and any row whose folder it cannot place on this seat; never delete a trigger the plan did not name, and never a trigger the listing did not carry.
- **`creates` and `updates` are NEVER executed here.** This step never deletes Morning Brief or Maintenance: the owner's ruling of 2026-11-09 (cr1#942) replaced the 2026-11-03 delete-and-recreate rule. They are the owner's own registration's (above), and that registration (`schedule_backend.plan_registration`) is the only create path for them: never create Morning Brief or Maintenance because a check of your own did not find it, which is how a seat came to run each twice. After that registration they are on the seat, so the plan keeps them (`current`). A chat the owner paused on this seat is kept with every copy still switched off: it is never deleted and never set up again (the scheduler's create takes no on/off switch), and the owner's registration updated its prompt only, so it stays paused (cr1#914). Write no trigger row for it here: its stored row, time included, stays as it is. A survivor the plan still lists under `creates` is one the owner's registration could not land; it is not created here.
- **The record**: ONE writer call naming every id in `result["gone_ids"]` whose removal you executed, which drops their trigger rows and writes the one config record the lateness ledger reads. `gone_ids` never names Morning Brief or Maintenance:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"source_skill": "<this skill>", "task_ids": [<every id of result["gone_ids"] whose removal was executed>], "workspace_root": "<WS>"}, "name": "schedule_backend:record_removals"}'
```

- **The line**: say ONE line in the summary, verbatim, or nothing when it is empty, composed from the deletes that actually ran: `schedule_handoff.owner_line(result, REMOVED_OK, REMOVED_FAILED)` (in-shell, like step 2's trim below). It equals `result["owner_line"]` when every planned delete ran, and its words are `schedule_backend.reinstall_line`'s, for exactly what this step does: a failed delete is never named as removed, it is named as not removed this time. Never count or name the chats yourself. It claims nothing this step did not do (cr1#970 review): it never names a survivor as set up fresh or as having taken this version's instructions, and a survivor the plan still lists under `creates` is named as not set up. Never say `result["reinstall_line"]` or a `result["lines_if_failed"]` line here: they describe the creates and prompt updates this step does not execute. The line keeps the paused sentence: a kept chat the owner paused is named in `result["paused_names"]` and the line says "<name> stays paused, as you left it." `result["duplicates"]` and `result["copies"]` name a task this folder runs twice; nothing is removed for either, and the line tells the owner to delete the extra copy. Never delete a copy yourself.

**Then take what step 2 removed out of the listing, before step 3 (cr1#970 review).** Every later step reads this one trimmed listing, so nothing just removed is paused, folded or offered for switch-off: an update call against a deleted chat is never made, and no line calls a deleted chat switched off. Take out every removal the plan named, whether its delete call succeeded or not (a failed delete is left for the next run, never paused in its place). When `ok` was false there were no removals and `RECORDS` is unchanged:

```python
import sys; sys.path.insert(0, "shared/scripts")   # cwd == $PLUGIN_ROOT per Rule 22
from schedule_handoff import records_after_removals
RECORDS = records_after_removals(RECORDS, result["removals"])
```

## Step 3: Offer to switch off an eliminated or renamed chat (SPEC LIFECYCLE1 §4; propose the removal, NEVER disable it silently)

This block fires only when a task in `schedule_config.RETIRED_TASKS` is still present in `RECORDS`. On the overwhelming majority of workspaces that set is empty and this whole block is a silent no-op: say nothing about a chat the customer never had. **It covers the ELIMINATION and RENAME classes ONLY (SPEC TASKRET1).** `propose_task_retirements` already filters the READINESS class out, because a readiness retirement is APPLIED by step 4, and offering to switch off a chat that the same run already switched off would read as the product not knowing its own state. If you find yourself about to offer a pause for one of the readiness-retired ids, something upstream is wrong; stop and check `schedule_config.retirement_class`.

```python
import sys; sys.path.insert(0, "shared/scripts")   # cwd == $PLUGIN_ROOT per Rule 22
from schedule_proposals import propose_task_retirements, log_retire_proposal
offers = propose_task_retirements(WORKSPACE_ROOT,
                                  registered_ids=<the task ids in RECORDS>,
                                  task_records=<RECORDS>)
```

**Pass `task_records`, not just the ids (EOD2 / REVIEW F-2).** The accept is the customer's own pause, which DISABLES, so the id stays in the registered set; ids alone cannot tell "has not acted" from "already acted", and the offer would repeat every six weeks for the life of the workspace. The records carry `enabled`, which is the difference.

For each returned offer, surface its `line` VERBATIM (one line, built from the registry so the wording cannot drift between here, change-schedule and system-health), then log the suppression record with `log_retire_proposal(WORKSPACE_ROOT, offer["task"])`. The helper already honours the 6-week window, so a customer who ignored the offer is not asked again on the next run.

**Never disable it yourself.** The customer's own pause is what switches it off, routed through change-schedule exactly like every other schedule change. A RENAME offer (`schedule_config.is_renamed_task(offer["task"])`, `past-meetings` to `end-of-day` the first) is surfaced the exact same way; the registry has already worded its line as a rename. `end-of-day` is shelved, so `add end of day` gets `schedule_config.shelved_line("end-of-day")` verbatim and registers nothing, and `schedule_config.is_task_served("end-of-day", registered_ids)` is why it never reads as missing. Never register `end-of-day` from this skill. Silence costs the customer nothing: a still-registered `past-meetings` keeps firing the current End of Day pack, because both ids read the same orchestrator file. Only step 2 removes it, and only on the cloud scheduler; the older desktop scheduler has no delete, so on that seat it stays until the customer says `pause past meetings`.

## Step 4: The readiness retirement (`readiness_retirement_v1`; SPEC TASKRET1, the owner's ruling 2026-08-17)

**4a. The release item (`v5310_readiness_retirement`, DOORS1 1.5).** The update bridge never runs this item (cr1#970); this step runs its action with the listing, so its pause handoff lands. One call, through the same plugin shell as the blocks below:

```python
import sys; sys.path.insert(0, "shared/scripts")   # cwd == $PLUGIN_ROOT per Rule 22
from release_actions.force_readiness_retirement import force_readiness_retirement
item = force_readiness_retirement(events_path, WORKSPACE_ROOT,
                                  {"task_records": <RECORDS>,
                                   "registered_ids": <the task ids in RECORDS>,
                                   "source_skill": "<this skill>"})
```

`item["ran"]` false is the answer on almost every run: say nothing (the action has written its own receipt). On `ran` true, apply the pause two-step (below) to each id in `item["context"]["pause_task_ids"]`, in order, recording each with `log_readiness_retirement(WORKSPACE_ROOT, <id>, source_skill="<this skill>")`, then say the ONE line in `item["context"]["summary"]`, verbatim. The item stays armed until a later run reads those pauses back from the listing and marks itself done; it never claims a pause that did not land.

Keep the ids of `item["context"]["pause_task_ids"]` as `ATTEMPTED_4A`, every one of them, whether its pause landed or not (an empty list when `ran` was false).

**4b. The readiness migration (`readiness_retirement_v1`).** The one change here that switches off a chat the customer can see, without asking. It is applied, then narrated: NEVER downgrade this to calibration_question, because a per-task question rebuilds the register-then-nag pattern the retirement removes. Everything about it is scoped to CLASS, never to a task name. The plan reads the activity log for its per-task fence and writes nothing:

```python
import sys; sys.path.insert(0, "shared/scripts")   # cwd == $PLUGIN_ROOT per Rule 22
from schedule_proposals import plan_readiness_retirements, log_readiness_retirement
from schedule_config import readiness_retirement_summary
plan = plan_readiness_retirements(registered_ids=<the task ids in RECORDS>,
                                  task_records=<RECORDS>,
                                  workspace_root=WORKSPACE_ROOT)
plan = [e for e in plan if e["task"] not in ATTEMPTED_4A]   # never a second pause attempt in one run
```

**`plan == []` is the answer on the overwhelming majority of workspaces, and it means SAY NOTHING.**

On a non-empty plan, for EACH entry, in order, the pause two-step (below) with `log_readiness_retirement(WORKSPACE_ROOT, entry["task"], source_skill="<this skill>")` as its record. A chat 4a tried to switch off in this run, whether the call landed or not, is never tried again here: the line above drops it. Then surface **exactly ONE line**, built by the registry, never composed here: `readiness_retirement_summary([e["task"] for e in plan])`. It names what was switched off, why it needed groundwork first, and what brings each chat back (`reoffer_when`). **Do not drop the comes-back clauses to shorten it, and do not paraphrase.**

**Pass `task_records`, not just the ids.** The records carry `enabled`, and an entry already disabled is dropped from the plan: that is the fence that stops a workspace from re-writing a `schedule_config_changed` event on every run forever. `workspace_root` is the other fence: a task that already carries this migration's own pause record is dropped, so each retired chat is switched off at most once per seat. **The one-shot is per task (cr1#540)**: never hand `readiness_retirement_v1` to an adjudication lookup and never log `workspace_migration_applied` for it.

**Do NOT touch anything else.** No `schedule_config` override key is deleted, no on-demand skill is disabled, and no other retirement class is applied here. If you are about to disable a task that `schedule_config.is_readiness_retirement` does not return True for, stop.

## Step 5: The fold (`fold1a_fire_fold_v1`; SPEC_FLOW1 Lane H / FOLD1A, the owner's ruling 2026-09-07) and the superseded silent tasks

Same two-step shape as step 4. `schedule_config.FOLDED_FIRES` is the membership test, `waiting-on` and `my-plate` are the two ids. Nothing here reads or writes a workspace file before the call that writes everything:

```python
import sys; sys.path.insert(0, "shared/scripts")   # cwd == $PLUGIN_ROOT per Rule 22
from release_actions.fold_scheduled_fires import plan, fold_scheduled_fires
planned = plan(WORKSPACE_ROOT, task_records=<RECORDS>)
```

**`planned["status"]` other than `would_fold` (`not_a_workspace`, `already_ran`, `nothing_to_fold`) is the answer on the overwhelming majority of runs, and it means SAY NOTHING.**

On `would_fold`, exactly TWO steps and ONE call shape. **Do not also call `apply_plan` by hand**: `fold_scheduled_fires` calls it itself, and a hand-rolled step before it writes a SECOND `schedule_config_changed` under a SECOND `brain_batch_id` that no receipt names (REVIEW_FOLD1A F-5).

1. For EACH task id in `planned["tasks_to_disable"]`, in order, execute the plan `schedule_backend.plan_update(task_id=<id>, enabled=False)` returns, then its `after` call; a `Refusal` stops the loop rather than half-applying it. Never pass `prompt`.
2. Only after EVERY call in the loop SUCCEEDED, make the ONE call that writes everything: `result = fold_scheduled_fires(events_path, WORKSPACE_ROOT, {"task_records": <RECORDS>, "now_iso": <now>, "source_skill": "<this skill>"})`. The update bridge never runs this item (cr1#970), so this is its one caller, and its rows name `<this skill>`. It writes the store and the audit event through `schedule_config.apply_fold_state` under ONE `brain_batch_id`, and the ONE receipt that is this seat's one-shot marker.

Then surface **exactly ONE line**, built by the module: `result["context"]["summary_line"]`. **Do NOT touch anything else**: `FOLDED_TASK_IDS` is exactly two, and the Inbox chat is untouched. If you are about to disable a task that `schedule_config.is_folded_fire` does not return True for, stop.

**The superseded silent tasks (MAINT1, D5).** For each task in `schedule_config.SILENT_TASKS`, every id in `SUPERSEDED_BY[task_id]` still in `RECORDS` and enabled is switched off with the pause two-step, its record `schedule_config.log_schedule_config_change(WORKSPACE_ROOT, [{'task_id': '<id>', 'cron': None, 'enabled': False}], source_skill='<this skill>')`. Schedule setup already did this at Phase 5.9, so there it plans nothing; from the Hub it is the same idempotent migration. When it switched something off, say once: *"Your background upkeep now runs as one 'Maintenance' entry. The old background entries are switched off."*

## The pause two-step (every switch-off in this file, and every `pause_task_ids`)

1. Execute the plan `schedule_backend.plan_update(task_id=<id>, enabled=False)` returns, then its `after` call. The seam picks the backend from this session's tool list. A `Refusal` instead of a plan means this seat cannot move the task: print nothing and skip that task. **Never pass `prompt`.**
2. Only after that call SUCCEEDED, write the substrate record named by the step (for a readiness retirement, `schedule_proposals.log_readiness_retirement`, a thin wrapper over `schedule_config.log_schedule_config_change`, THE one config-event writer, SPEC SCHED1 §0-4). Never hand-roll the event. The order is not a preference: an event claiming a task is off while it still fires is worse than no event, and `late_fire` reads these records to refuse scoring the slots the change itself minted (F-51).

Every pause writes its record through the one config-event writer the moment its call lands, never a hand-rolled event (SPEC SCHED1 §0-4): the lateness ledger READS `schedule_config_changed` to know that a slot older than the change was minted by the change and must never be scored, and a pause nobody recorded leaves it believing this task's newest config change is whatever came before it.

**Release items that act on scheduled chats (DOORS1 1.5; cr1#970).** The update bridge skips `v5300_fold_scheduled_fires` and `v5310_readiness_retirement` (`schedule_handoff.BRIDGE_SKIPPED_RELEASE_ITEMS`). Their actions run here, with `RECORDS` and `"source_skill": "<this skill>"` in the context: the readiness item in step 4a, the fold in step 5. Run no other release item from this file.

## Step 6: Prompt refresh and the cron re-anchor, for both owners (the owner's follow-up ruling, 2026-11-09)

The owner ruled that the prompt refresh ships with an update and is applied by both owners every time they run. One writer still holds: the skill running this file, one call after another on any one task, never a second chat.

- **From `enable-command-room-schedules`: nothing more to do here.** Its Step 1.C compared and refreshed every registered chat of this folder in this run, and its Step 1.C2 re-anchored any uncustomized cron; running either again would only plan nothing.
- **From `cockpit-setup`:** Step 2 refreshed the prompts of the four Hub tasks. Morning Brief and Maintenance come back from its plan `current` and not compared, because the listing rows it hands in carry no prompt, so this step compares them, with their prompts read from the listing (the Hub already owns their registration, so this is still the one writer). Take the listing again after steps 2 to 5 (or reuse step 1's when nothing was written to the scheduler since), then:
  1. **Prompt.** For every record of this folder that is not a Hub task (`cockpit-…`), Morning Brief and Maintenance included, call `schedule_refresh.plan_prompt_refresh(task_id, registered_prompt=<its prompt>, workspace_root=WORKSPACE_ROOT, plugin_version=<the installed plugin version>)`. `rewrite`: execute the plan `schedule_backend.plan_update(task_id, prompt=<the answer's composed>)` returns (prompt only, so time and on/off stay as they are), then write the one receipt `schedule_refresh.log_schedule_refreshed(WORKSPACE_ROOT, task_id, "prompt", schedule_refresh.prompt_fingerprint(<old prompt>), schedule_refresh.prompt_fingerprint(<composed>), source_skill="cockpit-setup")`. Keep in `REFRESHED` the task id of every rewrite whose update call succeeded and of every chat this step judged `current`: both are current now, and a chat step 2 planned an update for but this step judges current (the two compose with this run's own pair and folder) must not keep the marker back forever. `current` or `unknown`: nothing. `refuse`: say its `line` and change nothing. A stale Maintenance prompt (one that still types a retired command, or any body this version composes differently) is refreshed here, so a Hub run brings it current. Other chats of this folder are usually none on the cloud scheduler after step 2. Maintenance registers a stub (cr1#972): a seat still on its old full text is moved to the stub here once, and after that it is `current` on every run, whatever a later release changes in a maintenance job. When an update call fails, write no receipt and say `schedule_refresh.PROMPT_UPDATE_FAILED_LINE` once, verbatim; never another phrase. When Maintenance's update landed here, the Hub's final line (its Step 6) counts Maintenance as refreshed, never as left alone or not checked.
  2. **Cron.** Run schedule setup's Step 1.C2 exactly as written there: the one planner `schedule_backend:plan_default_cron_moves` through the read door, with `"source_skill": "cockpit-setup"` in its args, which moves a kept Morning Brief or Maintenance still at an old shipped default to the new one (cr1#967), with a time-only update naming its trigger, then lands each move's `landing`. It plans nothing for the Hub's own tasks, never a create, and never touches a time the owner set. Step 2 and item 1 sent no time for either chat, so this is the one time call on that task in this run.

## Step 7: Record that this version's schedule changes are applied

Last, once steps 2 to 6 ran (each may have planned nothing), write the one record that clears the update's line, through the write door, and paste what it prints. **Only when this run finished the work step 2 planned:** `schedule_handoff.may_record_applied(<step 2's result>, refreshed=REFRESHED, failed_removals=REMOVED_FAILED)` is true (no create left; every prompt update the plan lists was made in this run, by the owner's own registration or by step 6; no planned delete failed; `REFRESHED` is empty from schedule setup, whose Phase 3 already made its updates). Otherwise write no record: the update keeps naming `set up command room schedules` and `rebuild my hub` until a run finishes it. The record also carries a fingerprint of Maintenance's registered stub (cr1#972) and of the shelved, retired and superseded sets (`schedule_handoff.schedule_fingerprint`), so a later change to any of them brings the update's line back:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"plugin_version": "<the installed plugin version>", "source_skill": "<this skill>", "workspace_root": "<WS>"}, "name": "schedule_handoff:log_schedule_changes_applied"}'
```

Render it with `workspace_access.py plan run_writer --json '…'`. It writes one `schedule_changes_applied` row naming the installed version, and nothing on a second run of the same version. It says nothing: no line comes from this step. When step 1's readback failed, do not write it.

## Re-test: a retired maintenance job's stanza is gone from the registered prompt (DOORS1 1.1)

The release item `v5310_retire_age_out_job` switches the age-out job off in the workspace's own config and re-registers nothing; the registered Maintenance prompt loses the stanza through the prompt compare of either owner (schedule setup's Step 1.C, or step 6 of this file on a Hub run), never through the update (cr1#970). Since cr1#972 the registered prompt is a stub that carries no job stanza at all; its fire composes the current text.

> **Re-test line (v5.30.0 script, HOLD 2 / Part E):** after `set up command room schedules` or `rebuild my hub`, read the registered set through the seam, find the row whose `task_id` is `maintenance`, and look at what it carries. The listing is `schedule_backend.plan_list`'s normalised records; a legacy listing returns a `path` to the task's SKILL.md, never the prompt text — read the file at that `path`. Confirm the string `age-out` does not appear in it (on a backend whose listing carries the prompt itself, read that instead; a record whose prompt is `None` is unknown, never clean). If it does, the prompt compare did not run and the seat still types a command that no longer exists.
