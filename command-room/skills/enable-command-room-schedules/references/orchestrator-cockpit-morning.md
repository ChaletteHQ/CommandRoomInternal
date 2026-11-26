# Cockpit morning (taskId: cockpit-morning)

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this run is the Hub's morning, in one session (cr1#1722 step 2): the inbox, meeting prep, the day's first whole update and the morning brief, in that order. Each part is done by its own file in this folder, exactly as that file says; this file only says which parts run, in what order, and records each one on the page's `status/morning` light. It lands no page of its own. **The final response** is the one step 3 gives: the brief's post in full when this run did the brief, then, as its last line, `morning-finish`'s `line`; with no brief, that line alone. Then STOP.

**Why one session.** Hub Prep, Hub Inbox and the Morning Brief chat were three triggers, each a session of its own (owner rulings on cr1#1722: "the morning run runs by itself", merged into one). Hub Prep and Hub Inbox are retired; a prep or inbox press on the page fires this task with its part on the light (decision F, coordinator, 2026-11-24).

## Fixed, never chosen

- **The parts** are the `parts` that step 1 answers, in the order it gives them, and no other. Never add one, skip one or reorder them.
- **The run's id** is the `run_id` that step 1 answers. Never this session's id.
- **The zone** is the `tz` that `load_config` answers at step 0. Every `morning-start`, `morning-mark` and `morning-finish` below carries it as `--tz "<tz>"`: a part counts toward the morning only on the day it was marked, in the workspace's zone.
- **A press's run** (step 1 answers `pressed: true`) runs only the prep or inbox part it was asked for: never the brief, never the whole update. What it does never counts toward the morning being done, so the scheduled run still does every part.
- **No time is typed.** Each command below reads its own clock.
- **One part stopping is not the run stopping.** A part that ends on its own STOP, or whose call says no, is marked `error` with its line, and the run goes on to the next part.
- **Another run holding the light IS the run stopping.** A mark that answers `held: false` means another morning run holds the light now: a later one took this one over (it had gone 35 minutes with no mark at all), or a press's light was written over this run's claim. When that answer has `batches`, send its `batches[0]` as ONE `batch` (cr1#1782: it leaves the press's run the morning, which it does when its press is done; a refusal of that write is left alone). The whole turn is that answer's `line`; STOP at once: no further part, no brief, no finish, no receipt. The run that holds the light does what is left.
- **One meeting's prep.** When step 1 answers a `meeting_id`, the press is "Prep this one" (ruling 120): the `prep` part preps the meeting that press asked for and never the usual next four.

## 0. The page

Read `cockpit-shared.md` in this folder and do its shared step 0, once: the workspace, `load_config`, and the page's database tool. Keep `load_config`'s `tz`. It is not done again inside any part.

## 1. The start, HERE in this shell

`list` on `status` with `out_dir` set to a new folder in this session's own scratch, `<scratch>/page`. Its answer lists the documents it saved, each with its version: save that answer, exactly as it came, as its own file directly in that same folder (for example `answer-1.txt`). Never retype it and never put a version into a call yourself; the code reads it there. Then: An empty collection answers one line (`No documents matched in collection "<name>".`): save that line as its answer too, since it is the proof that nothing is there, and without it nothing new is created.

```bash
python3 "$RT/shared/scripts/cockpit_helpers.py" morning-start --reads "<scratch>/page" --out "<scratch>/start" --tz "<tz>"
```

It prints one JSON line. `stop: true`: another morning run holds the light, nothing is left to do this morning, a press named no part of its own, or (`due: true`) a press's run holds the light and does the morning when its press is done. When its `batches` is not empty, send `batches[0]` as ONE `batch` first (it puts the page's own light back, so the page never waits on a run that did nothing, or leaves the press's run its morning); a refusal of that write is left alone. Then the whole turn is its `line`; STOP. Otherwise send `batches[0]` (this run's claim of `status/morning`) as ONE `batch`. Refused because `status/morning` changed: the whole turn is "Another morning run started a moment ago; this one stopped without changing anything."; STOP.

## 2. Each part, in order

For each part in `parts`, first say it has started, so the light shows the run is alive: `get` on `status/morning` with `out_dir` `<scratch>/begin-<part>` (its answer saved there as at step 1), then:

```bash
python3 "$RT/shared/scripts/cockpit_helpers.py" morning-mark --reads "<scratch>/begin-<part>" --out "<scratch>/begin-<part>/out" --run-id "<step 1's run_id>" --part "<part>" --state "started" --tz "<tz>"
```

Send its `batches[0]` as ONE `batch`. `held: false`: STOP, as above. Then do the part:

- **`inbox`**: `orchestrator-cockpit-inbox.md`, from its shared step 1 to its last step, exactly as written.
- **`prep`**: `orchestrator-cockpit-prep.md`, from its shared step 1 to its last step, exactly as written. With a `meeting_id` from step 1 (a "Prep this one" press, ruling 120), the run is the one that file's own "Prep this one" section narrows: that section reads the press waiting on the page and keeps its own refusals, its helper call carries step 1's `run_id` (only the press that started this run is judged; one another run was started for is left as it was), and nothing here adds a second way to pick a meeting. When that section finds no press waiting (its helper's `none: true` included, cr1#1825), its helper's call is refused, or the file has no such section, prep nothing: the part's line is "No Prep this one press was waiting, so nothing was prepped.", marked `error`. Never fall back to the next four on a one-meeting press.
- **`update`**: `orchestrator-cockpit-update.md`, from its step 2 to its last step, exactly as written (its step 1 is this file's step 0), except that its step 3's `update-start` carries `--whole`: the Morning's update always builds everything, even when a press on the page asked for less. It publishes the off-limits list at its step 3b, after that claim and before any heavy part. When that file stops on its own busy line (another update is running now), keep that line as the part's line, as for any part: the mark reads it. Behind an update that builds everything, the mark counts the part as done, because that update builds the page (ruling 129). Behind one that saves only the latest changes, the mark counts it as not done, and the next morning fire runs it.
- **`brief`**: `orchestrator-morning-brief.md`, from its first step to its last, exactly as written, except that its post is not said yet: hold it, word for word, for the final response at step 3. Its file's STOP ends the part, never this run: the brief is marked, and the finish is sent, before anything is posted.

**A long part marks as it goes.** The 35-minute rule counts from the light's last mark, so a part that runs long says it is still alive: in `prep`, after each meeting's brief is written, and in `inbox`, after each batch of mail is sorted, run the `started` mark above again for that part (`get` into `<scratch>/begin-<part>-<n>`, `<n>` counting up, its answer saved there as at step 1, then the same command) and send its `batches[0]` as ONE `batch`. `held: false` there: STOP, as above.

Each part's file ends with a final line and STOP: here that line is kept as the part's line, and its STOP ends the part, not this run. Then record the part:

`get` on `status/morning` with `out_dir` `<scratch>/mark-<part>` (its answer saved there as at step 1), then:

```bash
python3 "$RT/shared/scripts/cockpit_helpers.py" morning-mark --reads "<scratch>/mark-<part>" --out "<scratch>/mark-<part>/out" --run-id "<step 1's run_id>" --part "<part>" --state "<done, or error when the part stopped before its last step>" --line "<the part's line>" --tz "<tz>"
```

Send its `batches[0]` as ONE `batch`. `held: false` (another run holds the light now): STOP, as above. Any other `ok: false` is left alone: go on to the next part.

## 3. The finish, then the final response

`list` on `status` with `out_dir` `<scratch>/finish`, its answer saved there as at step 1 (the update's light comes with the morning's: a part handed to another update counts only while that update has not failed), then: An empty collection answers one line (`No documents matched in collection "<name>".`): save that line as its answer too, since it is the proof that nothing is there, and without it nothing new is created.

```bash
python3 "$RT/shared/scripts/cockpit_helpers.py" morning-finish --reads "<scratch>/finish" --out "<scratch>/finish/out" --run-id "<step 1's run_id>" --tz "<tz>"
```

`held: false`: STOP, as above, with no receipt. `carry_on: true`: a morning fire stopped behind this press's run and left it the morning; send its `batches[0]` as ONE `batch` (this run's light is now the morning's), then do its `parts` exactly as step 2 says, and come back to this step, into `<scratch>/finish-2` (and so on). Otherwise send its `batches[0]` as ONE `batch`. Then the run's one receipt (`seconds` is that answer's `elapsed_s`, how long the run took from its claim; leave the key out when it is null):

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"counts": {"morning": {"parts": <how many parts ran>, "seconds": <morning-finish's elapsed_s>}}, "fired_via": "<scheduled or manual, from shared step 0>", "legs": ["morning"], "task_id": "cockpit-morning", "workspace_root": "<WS>"}, "name": "cockpit_helpers:log_run_receipt"}'
```

The final response is the brief's post held at step 2, in full, when this run did the brief, and then, as its last line, `morning-finish`'s `line`; with no brief, that line, one line, and nothing else. This run never switches a scheduled task on or off (cr1#970): when Maintenance is off, the page switches it back on while the owner is there (owner ruling 116).
