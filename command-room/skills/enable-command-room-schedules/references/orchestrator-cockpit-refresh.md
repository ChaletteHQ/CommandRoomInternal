# Cockpit refresh (taskId: cockpit-refresh)

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this run writes the cockpit page's plate feed from the book. It writes the book nothing but the plate's display numbers (building the plate mints them), and the feed through the artifact database tool. It posts NO widget and lands NO page of its own: the chat turn is one line, then STOP. The page is the surface; this run is one of its four engines (COCKPIT1, cr1#608; four tasks per cr1#619).

**The whole build is four calls beside the data, in order (cr1#1530, cr1#1638, cr1#1714).** Everything below runs in its order, and nothing else is read to do it: not `cockpit-shared.md`, not another task's file, not the workspace's brain file, no writing rules and no context loader (a refresh writes no words of the owner's). The sections after "When the one call is refused" are the same work in steps, for that case only.

## Fixed, never chosen

- **`<WS>`** is the `ws` field of the device's discover answer (the bootloader's Step 1.3). **`<RT>`** is the runtime the bootloader resolved. Every device call below is rendered and run the way the bootloader renders its own.
- **The run's id** is the `run_id` that step 3 answers. Never this session's id.
- **No time is typed.** No call below passes a time: each helper and command reads its own clock.
- **The page's documents stay in files.** Every read below saves them with `out_dir` in this session's own scratch, and each read's answer, which lists the versions, is saved as it came beside them; only code reads those files. Never type a page document, a version or a choice into a call.
- **Every answer passes through unchanged.** `batches` go to the page exactly as printed; parts are saved whole.
- **The run mode** is `scheduled` unless a human message exists in this session, then `manual`; when uncertain, `manual`.

## 1. The page

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:load_config"}'
```

`found: false`: say "No Command Room Hub is set up for this folder; say `set up my hub` in a Command Room chat." and STOP. Load the page's database tool (`ArtifactData`; a listed deferred tool counts as present) and use the answer's `url` for every read and write. With no such tool, say "The Hub page could not be reached from this run." and STOP.

## 2. The page, read into files: three reads in one turn

Each with `out_dir` set to the same new folder in this session's own scratch, `<scratch>/page`: `list` on `status`; `query` on `choices` with `where: [["applied", "==", true]]` and `limit: 1000`; `list` on `feed`. Each answer lists the documents it saved, each with its version: save each answer, exactly as it came, as its own file directly in `<scratch>/page` (a new name each time, for example `answer-1.txt`). Never retype an answer and never put a version into a call yourself; the code reads them there. An empty collection answers one line (`No documents matched in collection "<name>".`): save that line as its answer too, since it is the proof that nothing is there, and without it nothing new is created.

## 3. The start, HERE in this shell

```bash
python3 "$RT/shared/scripts/cockpit_helpers.py" refresh-start --reads "<scratch>/page" --out "<scratch>/start"
```

It reads the saved page in code and prints one JSON line. `ok: false`: the start could not run; the whole turn is its `line`; STOP, writing nothing. `stop: true`: another refresh holds this task's light now; the whole turn is its `line`; STOP, writing nothing. Otherwise send `batches[0]` (this run's claim of its own light, the page's own run id when the page fired this run) as ONE `batch`. Refused because `status/refresh` changed: another refresh claimed it a moment ago; the whole turn is "Another refresh is running now; this one stopped without changing anything."; STOP. Then send each later list in `batches` as ONE `batch` (the lights a dead run left, and the old applied choices to clear); a refusal there is left alone, the next refresh does it.

## 4. Four writer calls, in this order

The build is four parts, each its own call, so no call needs more than the door's time on a seat whose folder is read over the computer's mount (cr1#1714: the whole refresh in one call timed out there on every run). First the plate:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"run_id": "<step 3's run_id>", "stage": "plate", "workspace_root": "<WS>"}, "name": "cockpit_helpers:refresh_all"}'
```

Its answer carries `token` and `next`. Then each next part with that token, `reads`, then `review`, then `record`, one call each, never two in one call:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"run_id": "<step 3's run_id>", "stage": "<the answer's next>", "token": "<the plate's token>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:refresh_all"}'
```

A part that answers `not_finished` (it ran past its time and stopped itself, keeping nothing), or a `reads` or `review` call that fails, times out or answers `ok: false`, is NOT retried: go on to the next part, with the token its answer or the plate's carries; the `record` call names what did not finish on the light. The `record` answer is the run's: `token` and `parts` (what to pull next), `pages` (each document, whether it was built, its size and its seconds), `failed` (the documents left out), `counts` and `line`. This task's own light is one of its documents, so the finish lands with the feeds. A `record` call that fails or times out with no `token` is called once more, with the plate's token (review of #1733): on a run that has finished it answers that run's `parts` and light again, so the retry still publishes. A `plate` call, or that second `record` call, that fails with no `token` is "When a call says no".

## 5. Pull each part, one call per part

Numbered from 0 to one less than the length of `parts`:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"part": <the part's number>, "token": "<the token>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:refresh_part"}'
```

One part per call, never two in one call. A part must reach a file in this session's own scratch WHOLE: when the tool saved the answer to a file, that file is the part; when the answer came back in the turn, save it to a file exactly as it came. Never retype, shorten or re-compose a part, and never save one in the workspace.

## 6. The page again, then unpack HERE

The claim changed `status/refresh`, so read the page's versions again into the same folder, in one turn: `list` on `status` and `list` on `feed`, each with `out_dir` `<scratch>/page`, each answer saved as at step 2. Then, in this shell: An empty collection answers one line (`No documents matched in collection "<name>".`): save that line as its answer too, since it is the proof that nothing is there, and without it nothing new is created.

```bash
python3 "$RT/shared/scripts/cockpit_helpers.py" unpack-refresh --out "<scratch>/out" --token "<the token>" --parts <how many parts> --versions "<scratch>/page" "<each saved part file>"
```

It takes every version it needs from those saved answers; there is nothing to pin by hand. It answers `ok: false` with `unversioned` or `unread_answers` when a document the page holds has no version in the saved answers: send nothing, pulling a part again will not help; go to "When a call says no" with its `line`. An answer with `note` (cr1#1763: the page's read ended with a line this version does not know, so a document it did not list was not created; the rest is in the batches) is sent as any other, and its `note` ends this run's final response, word for word, after the line it would have said. It answers `ok: false` with `missing` when a part did not arrive whole: pull that part again and run this again; send nothing until it answers ok. Then send each batch as ONE `batch`, exactly as printed; this task's light comes last. A batch refused because a document changed names that entry: another run wrote it a moment ago, so it is current; send the batch again WITHOUT that entry, once per named entry. Never drop a pin to force a write.

**A batch not sent for any other reason means the light is written as `error`, never `done`** (the light is in the last batch, so a refused batch never leaves a `done` over feeds that did not land, and a refused last batch never leaves the light claimed and running). Write it through the `status_doc` call in "When a call says no", with one sentence naming the documents that did not land, `run_id` step 3's run id, and `set` it on `status/refresh` pinned to the version a `get` on `status/refresh` shows now. The final response is that sentence.

## 7. The receipt, then the final response

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"counts": {"refresh": <the answer's counts>}, "fired_via": "<scheduled or manual>", "legs": ["refresh"], "task_id": "cockpit-refresh", "workspace_root": "<WS>"}, "name": "cockpit_helpers:log_run_receipt"}'
```

The final response is the answer's `line` (then the unpack's note, if any), and nothing else. A document under `failed` was not built or was too large to pull; the page keeps its old copy, and it is NOT retried in this run.

## When a call says no

The `record` call answering `ok: false` with a `token` (a part that did not finish, a document that did not update, or a `record` that answers `not_finished`, past its own time) still sends its parts as above: its light is the error light with its sentence, naming what did not land. A `plate` or `record` call that fails with no `token` (cr1#1727: the same words as step 4) writes the error light through this one call, then the receipt, and the final response is that line:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"leg": "refresh", "line": "<the answer's line, or the reason the call failed>", "run_id": "<step 3's run_id>", "state": "error", "stop_reason": "<the same line>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:status_doc"}'
```

`set` its answer on `status/refresh`, pinned to the version a `get` on `status/refresh` shows now.

**When the one call is refused.** An answer that says the name is not on the list (a runtime staged before this call existed), or a `plate` call refused because `stage` is not one of its arguments (a runtime staged before the four calls existed), means the four calls cannot run here. Only then, do the feed sections below in their order, one call per feed. Never put two feed reads in one call: that is what was cut off. Step 3 has already run, so:

- **Skip "First: settle, then clear the old choices":** step 3's batches did both, from the saved reads.
- **Skip `cockpit-shared.md`'s shared steps 0 and 1:** no Inbox phase, no loader, and the light is NOT rewritten. Step 3's claim is the running light, with step 3's `run_id` and its `claimed_by`; every write below keeps that run id, never this session's id.
- **The finish** is the `status_doc` call in "When a call says no" with `state` `done` and the plate's line (`error` and the sentence when a feed did not land), `run_id` step 3's run id, `set` on `status/refresh` pinned to the version a `get` on `status/refresh` shows now; then step 7's receipt.

## First: settle, then clear the old choices

Step 3 does both, from the saved reads: `refresh-start` runs `cockpit_helpers:settle_statuses` on the lights a dead run left, then `cockpit_helpers:plan_prune_choices` on the old applied choices, and its later batches carry them (the deletes as one batch). No step of this run does them again.

## The leg: the plate

The scheduled fire runs half past the hour from 7 AM to 3 PM so the page is current when the owner opens it; the page fires it after `cockpit-apply` turns green, so the plate it reads carries the closes. Either way the leg is one writer call:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_plate"}'
```

`set` the answer on `feed/plate` (a large answer goes through `file_path`: save it in this session's own scratch first, never in the workspace; pinned by the version rule in `cockpit-shared.md`). The answer carries the header, the focus rows, the chase and schedule rows, the whole plate capped, and the closes of the last 24 hours. The finish's line: "N on the plate, M closed in the last day." (the finish bullet above is the only light write)

**Then the work streams and priorities (cr1#660, cr1#1013).** One read beside the data, after the plate, so its counts match the closes the plate just showed. It reads the thread, work-stream and priority cards, the open list and the loose-ends list's items on no thread (the Work streams tab's "No home yet" section, cr1#1013 part 3), and feeds both the Work streams tab and the Priorities tab:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_projects"}'
```

`set` the answer on `feed/projects` (the same `file_path` rule for a large answer; pinned by the version rule in `cockpit-shared.md`). It never changes the light: a failed projects read leaves the old feed in place, and the light says what the plate said.

**Then the Week board (cr1#1017).** One read beside the data, after the work streams: the next seven days' due items from the open list and one lane per confirmed priority, for the Week view on Today (its meetings come from the Today feed's `week`, which the prep run writes):

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_board"}'
```

`set` the answer on `feed/board` (the same `file_path` rule for a large answer; pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the Today lists (cr1#662).** The owner's active reminders and the priority to-dos they added on the page, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_mine"}'
```

`set` the answer on `feed/mine` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the mail rules (cr1#1020).** The owner's mail rules in force and the threads dropped from the inbox, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_mail_rules"}'
```

`set` the answer on `feed/mail_rules` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the people (cr1#1020).** Everyone on file with what is open between the owner and them, and who is starred to Home, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_people"}'
```

`set` the answer on `feed/people` (the same `file_path` rule for a large answer; pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the morning brief (cr1#1310; behind the Hub's Brief button since cr1#1537, the Brief tab before).** Today's morning brief as the Morning Brief run saved it, one read beside the data. It reads that one saved file and nothing else, so the panel shows exactly what the brief posted:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_brief"}'
```

`set` the answer on `feed/brief` (the same `file_path` rule for a large answer; pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then To confirm (cr1#779; the Review tab until cr1#1286).** The open questions and proposed closes, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_review"}'
```

`set` the answer on `feed/review` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the Record tab (cr1#780; the Done tab until cr1#1537).** The recap's sections first, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "friday_wrap_helpers:week_sections"}'
```

Then hand its answer to the feed, and `set` that answer on `feed/week`:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"sections": <the week_sections answer>, "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_week"}'
```

It is pinned by the version rule in `cockpit-shared.md`. Like the projects, it never changes the light.

**Then Settings (cr1#781; behind the Hub's gear since cr1#1537, the Settings tab before).** Everything learned and every setting, each with its source, read from the stores the skills read, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_settings"}'
```

`set` the answer on `feed/settings` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the Still yours? review (cr1#1706).** The review's own small document, read as its writers left it (it is built by Maintenance once a day and by the review's own presses, never here), one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_stillyours"}'
```

`set` the answer on `feed/stillyours` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the follow-ups waiting (cr1#1745).** The follow-up drafted after each meeting and still waiting for the owner's press, each with its words, checked against who is off limits as that stands now, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_followups"}'
```

`set` the answer on `feed/followups` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then Today's People follow-ups (cr1#1789).** The follow-ups and the key relationships under People on Today, ranked in code, nobody off limits named, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_pfollow"}'
```

`set` the answer on `feed/pfollow` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then Urgent (cr1#1786).** The ranking over what the last inbox read saved, checked against who is off limits as that stands now, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_urgent"}'
```

`set` the answer on `feed/urgent` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the prep edits (cr1#1788).** The Prep page's own edits (the agenda, the Goal, the call guide's adds and removes), from their own small store, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_prepx"}'
```

`set` the answer on `feed/prepx` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then Focus (cr1#1785).** The list the owner picks: what is in it, in the owner's order, from its own small store, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_focus"}'
```

`set` the answer on `feed/focus` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then All open (cr1#1785).** Every open item, lean, so To do's search finds one past the plate's cap, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_allopen"}'
```

`set` the answer on `feed/allopen` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the voice rules the page writes in (cr1#1812).** The owner's main voice file, and email-writer's learned block when it holds more than the template, for the page's own drafts, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_voice"}'
```

`set` the answer on `feed/voice` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the Maintenance record (cr1#666).** When the Maintenance chat last finished, each job's last completion and how many hours it is behind, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:maintenance_status"}'
```

`set` the answer on `status/maintenance` (pinned by the version rule in `cockpit-shared.md`), unless it answers `idle` while the page's document says `done`: that is a read that failed, and the page keeps what it has. It is not one of the four lights: it records when Maintenance last kept the book up, the page shows its line only when a job failed or a connector went unread (cr1#822), and Go runs Maintenance on every press whatever it says (cr1#682). The document names the fire that left the note (`fired_via`, `fired_at`, cr1#743): a scheduled fire's word reaches the page here, at the next Refresh or Go, exactly as a typed run's does. Like the projects, it never changes this task's light.
