# Cockpit update (taskId: cockpit-update)

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this run is the one writer from the cockpit page to the book, and the one builder of the page from the book. It saves the choices the page holds through the owning writers, writes each result back to the page, then rebuilds the page's feeds, in that order. It posts NO widget and lands NO page of its own: the chat turn is one line, then STOP (COCKPIT1, cr1#608; Apply and Refresh as one run, cr1#1722).

**The whole run is the save, then the rebuild (cr1#1722).** `cockpit_helpers:update_claim` claims what waits and `cockpit_helpers:update_save` writes every choice this run claimed (commitments, with every close in one write; review, week, settings, mail rules, people, the Today page's own presses; the page's own sends, drafts and meeting changes). `cockpit_helpers:update_build` then rebuilds the page, one call per part: everything on a scheduled run, only what the saved presses changed on a run the page started after a press. `cockpit_helpers:update_finish` writes the run's one receipt. There is no second task to wait for and no claim to wait behind. Read nothing else to do it: not `cockpit-shared.md`, not another task's file, not the workspace's brain file, no writing rules and no context loader. `cockpit-apply-detail.md` is read only when step 5 says so.

## Fixed, never chosen

- **`<WS>`** is the `ws` field of the device's discover answer (the bootloader's Step 1.3). That answer has no `vm_path`. **`<RT>`** is the runtime the bootloader resolved. Every device call below is rendered and run the way the bootloader renders its own.
- **The run's id** is the `run_id` that step 3 answers. Never this session's id, local or remote.
- **The run's scope** is the `scope` that step 3 answers (`all` or `touched`). It is passed to step 4 as it came and never chosen here.
- **The last whole rebuild** is the `full_at` that step 3 answers (a time, or empty when none is on record). It is passed to step 4 as it came, never typed from anywhere else.
- **The senders and the check** are the `inbox_senders` (an object, often empty) and `start_check` that step 3 answers. Both are passed to step 4 exactly as they came, with `scope` and `full_at` beside them: the claim recomputes the check, and any value changed, added or left out makes the run rebuild everything.
- **No time is typed.** No call below passes a time, and no write carries a time made by hand: each helper and command reads its own clock.
- **No list of feeds is typed.** What the rebuild builds is decided in code from what the save kept; step 6 passes no list.
- **Every answer passes through unchanged.** The documents go in exactly as read; `writes` and `batches` go to the page exactly as they came back; parts are saved whole. Never rebuild a claim, a plan, a result or a pin from the page's data.
- **The page's own sends and meeting changes** are claimed by `update_claim` and recorded by `update_save`, from the documents read at step 2 and step 5. There is no separate check to run.
- **The run mode** is `scheduled` unless a human message exists in this session, then `manual`; when uncertain, `manual`. Pass it as `fired_via`.

## 1. The page

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:load_config"}'
```

`found: false`: say "No Command Room Hub is set up for this folder; say `set up my hub` in a Command Room chat." and STOP. Load the page's database tool (`ArtifactData`; a listed deferred tool counts as present) and use the answer's `url` for every read and write. With no such tool, say "The Hub page could not be reached from this run." and STOP: the choices live on the page.

## 2. The page's reads: six reads in one turn

Three into files, each with `out_dir` set to the same new folder in this session's own scratch, `<scratch>/page`: `list` on `status`; `query` on `choices` with `where: [["applied", "==", true]]` and `limit: 1000`; `list` on `feed`. A fourth into files with `out_dir` `<scratch>/page/waiting`: `query` on `choices` with `where: [["applied", "==", false]]` (cr1#1701: the start reads which messages the waiting presses name). Only the file names come back from those, which is all this run needs of them.

Two as read, kept for step 4: `query` on `choices` with `where: [["applied", "==", false]]`; `query` on `choices` with `where: [["by_page", "==", true]]`.

## 3. The start, HERE in this shell

```bash
python3 "$RT/shared/scripts/cockpit_helpers.py" update-start --reads "<scratch>/page" --out "<scratch>/start"
```

It reads the saved page in code and prints one JSON line. `ok: false`: the start could not run; the whole turn is its `line`; STOP, writing nothing. `stop: true`: another update holds this task's light now; the whole turn is its `line`; STOP, writing nothing: the presses that wait are saved by the run that holds it, or by the next one. Otherwise send `batches[0]` (this run's claim of its own light, the page's own run id when the page started this run) as ONE `batch`. Refused because `status/update` changed: another update claimed it a moment ago; the whole turn is "Another update is running now; this one stopped without changing anything."; STOP. Then send each later list in `batches` as ONE `batch` (the lights a dead run left, and the old applied choices to clear); a refusal there is left alone, the next update does it.

## 3b. The off-limits list, first

Only a run that WON its claim at step 3 gets here, so a run that stopped never publishes. Before the claim of the presses, the save and every part of the rebuild, publish the off-limits list on its own (cr1#1765), so a part that runs out of time later can never take it down. First `list` on `feed` with `out_dir` `<scratch>/wall/page` (only the file names come back) and save that answer to `<scratch>/wall/list.txt` exactly as it came, then:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_wall"}'
```

Save its answer to `<scratch>/wall/answer.json` exactly as it came (when the tool saved the answer to a file, copy that file there unchanged). Never retype or shorten it: its `digest` must match its document. Then, in this shell:

```bash
python3 "$RT/shared/scripts/cockpit_helpers.py" wall-batch --answer "<scratch>/wall/answer.json" --stored "<scratch>/wall/page" --out "<scratch>/wall"
```

When that `list` answered and listed no documents (a new page), run the same command with `--stored-empty "<scratch>/wall/list.txt"` in place of `--stored "<scratch>/wall/page"`; never use it after a `list` that failed or was not made. The helper checks the saved answer itself and publishes nothing unless it says no documents were listed.

Send its `batches[0]` as ONE `batch`, exactly as printed. Nothing here stops the run: a helper that fails, a `wall-batch` that answers `ok: false` (an answer that does not match its digest, a list that is not a list, a list dated more than five minutes ahead of this computer's clock, no saved page read, or a page that already has a newer list), or a batch that is refused is left alone, and the run goes on to step 4. The page keeps the copy it has, and lists no new mail by itself from a copy more than a day old.

## 4. The claim

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"fired_via": "<scheduled or manual>", "full_at": "<step 3's full_at>", "inbox_senders": <step 3's inbox_senders>, "page": [<the by_page documents as read>], "run_id": "<step 3's run_id>", "scope": "<step 3's scope>", "start_check": "<step 3's start_check>", "waiting": [<the applied == false documents as read>], "workspace_root": "<WS>"}, "name": "cockpit_helpers:update_claim"}'
```

`nothing: true`: nothing waits; go to step 6, skipping step 5. Otherwise send its `writes` as ONE `batch`. A batch refused because a document changed (another run claimed it, or the owner pressed Undo): step 2's two as-read queries again, then this call again, once.

## 5. The save

Step 2's two as-read queries again (the claim changed the documents), then:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"fired_via": "<scheduled or manual>", "n_more": <update_claim's n_more>, "page": [<the by_page documents as read>], "run_id": "<step 3's run_id>", "waiting": [<the applied == false documents as read>], "workspace_root": "<WS>"}, "name": "cockpit_helpers:update_save"}'
```

Send each list in its `batches` as ONE `batch`, in order. It answers no light: this run's light is written at step 8, with the documents. A batch refused because a document changed: the two as-read queries again, then

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"docs": [<both choices queries as read>], "status": null, "workspace_root": "<WS>", "writes": [<the refused batch, as update_save answered it>]}, "name": "cockpit_helpers:apply_repin"}'
```

and send its `writes` as one `batch`, once. A second refusal is left alone: the press stays waiting and the next update saves it.

When the answer carries `wall_again: true` (the save changed the mail rules, or it cannot tell what it changed), do step 3b again now, before anything below.

`next: detail` means a connector part is left, named by choice id: a confirmed send or draft (`connectors.inbox`) or a "Missed something?" search (`connectors.miss`). Only then read `cockpit-apply-detail.md` and do ITS STEP 1 AND ITS STEP 3 ONLY, with these three differences: the run's id there is step 3's `run_id` here; `status` in its `apply_repin` call is `null`; and its step 2 (the feeds) and its step 4 (`apply_finish`, the Apply light and the Apply receipt) are NEVER run from this file, because step 6 rebuilds the feeds and step 9 writes this run's receipt. Keep its `{<choice id>: <its result>}` for step 6. A step there that fails is "When a call says no" below, with that step's line.

## 6. The rebuild: as few writer calls as time allows

First the plate's call, which goes on to the later parts while there is time (cr1#1808). `results` is step 5's `{<choice id>: <its result>}` from the connector part, or `null` when there was none:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"results": <the connector part's results, or null>, "run_id": "<step 3's run_id>", "stage": "all", "workspace_root": "<WS>"}, "name": "cockpit_helpers:update_build"}'
```

When its answer is the `record` part's (`stage: "record"`), the rebuild is done: go to step 7. Otherwise its answer carries `token` and `next`: call the part it names as `next`, with that token, and the part THAT answer names, until the `record` part has answered. Every command here is the one printed in this file, never one written by hand. One call each, never two in one call, and never a part the answers did not name: on a run the page started after a press, a part with nothing to rebuild is skipped by `next`, and `record` always runs.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"run_id": "<step 3's run_id>", "stage": "<the answer's next>", "token": "<the plate's token>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:update_build"}'
```

A part that answers `not_finished` (it ran past its time and stopped itself, keeping nothing), or a `reads` or `review` call that fails, times out or answers `ok: false`, is NOT retried: go on to the part its answer names as `next` (`record` when it names none), with the token its answer or the plate's carries; the `record` call names what did not finish on the light. The `record` answer is the run's: `token` and `parts` (what to pull next), `pages`, `failed`, `counts` and `line`. This task's own light is one of its documents, so the finish lands with the feeds. A `plate` call that fails with no `token`, or a `record` call that fails with no `token`, is "When a call says no".

## 7. Pull each part, one call per part

Numbered from 0 to one less than the length of `parts`:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"part": <the part's number>, "task": "update", "token": "<the token>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:refresh_part"}'
```

One part per call, never two in one call. A part must reach a file in this session's own scratch WHOLE: when the tool saved the answer to a file, that file is the part; when the answer came back in the turn, save it to a file exactly as it came. Never retype, shorten or re-compose a part, and never save one in the workspace.

## 8. The page again, then unpack HERE

The claim changed `status/update`, so read the page's versions again into the same folder, in one turn: `list` on `status` and `list` on `feed`, each with `out_dir` `<scratch>/page`. Then, in this shell:

```bash
python3 "$RT/shared/scripts/cockpit_helpers.py" unpack-refresh --out "<scratch>/out" --token "<the token>" --parts <how many parts> --versions "<scratch>/page" "<each saved part file>"
```

It takes every version it needs from those saved reads; there is nothing to pin by hand. It answers `ok: false` with `missing` when a part did not arrive whole: pull that part again and run this again; send nothing until it answers ok. Then send each batch as ONE `batch`, exactly as printed; this task's light comes last. A batch refused because a document changed names that entry: another run wrote it a moment ago, so it is current; send the batch again WITHOUT that entry, once per named entry. Never drop a pin to force a write.

**A batch not sent for any other reason means the light is written as `error`, never `done`**: that is "When a call says no" below, with one sentence naming the documents that did not land.

## 9. The receipt, then the final response

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"fired_via": "<scheduled or manual>", "run_id": "<step 3's run_id>", "token": "<the token>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:update_finish"}'
```

The final response is the answer's `line`, one line, and nothing else. A document under the `record` answer's `failed` was not built or was too large to pull; the page keeps its old copy, and it is NOT retried in this run.

## When a call says no

The `record` call answering `ok: false` with a `token` (a press that could not be saved, a part that did not finish, a document that did not update) still sends its parts as above: its light is the error light with its sentence, and step 9 follows as written.

An answer with `ok: false` from `update_claim` or `update_save`, a `plate` answer with no `token`, a `plate` or `record` call that fails, or a batch at step 8 that could not be sent: one call, then `set` its `light` on `status/update` pinned to the version a `get` on `status/update` shows now, say its `line`, STOP. The light never stays on running.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"error": "<the line of that answer, or the reason the call failed>", "fired_via": "<scheduled or manual>", "run_id": "<step 3's run_id>", "token": "<the token, or empty>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:update_finish"}'
```

A press that was claimed and not saved before the run stopped stays on the page as it was pressed: its claim lapses after twenty minutes, and the next update saves it.
