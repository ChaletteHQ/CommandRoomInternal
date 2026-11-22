# Cockpit apply (taskId: cockpit-apply)

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this run is the one writer from the cockpit page to the book. It reads the choices the page saved and applies them through the owning writers, writes each result back to the page, and nothing else. It posts NO widget and lands NO page of its own: the chat turn is one line, then STOP (COCKPIT1, cr1#608; four tasks per cr1#619).

**The whole run is two calls (cr1#1608).** `cockpit_helpers:apply_start` claims what waits, or records the run and stops when nothing waits; `cockpit_helpers:apply_run` plans and writes every choice this run claimed (commitments, with every close in one write; review, week, settings, mail rules, people, the Today page's own presses; the page's own sends, drafts and meeting changes) and answers the page's writes ready to send. Read nothing else to do it: not `cockpit-shared.md`, not another task's file. `cockpit-apply-detail.md` is read only when step 5 says so.

## Fixed, never chosen

- **`<WS>`** is the `ws` field of the device's discover answer (the bootloader's Step 1.3). That answer has no `vm_path`. **`<RT>`** is the runtime the bootloader resolved. Every call below is rendered and run the way the bootloader renders its own.
- **The run's id** is the `run_id` that `apply_start` answers. Never this session's id, local or remote.
- **No time is typed.** No call below passes `now_iso`, and no write carries a time made by hand: each helper reads its own clock.
- **No writing rules and no context loader.** Apply writes nothing in the owner's words, so it loads neither.
- **Every answer passes through unchanged.** The documents go in exactly as read; `writes`, `batches` and `light` go to the page exactly as they came back. Never rebuild a claim, a plan, a result or a pin from the page's data.
- **The page's own sends and meeting changes** are claimed by `apply_start` and recorded by `apply_run`, from the documents read at step 2 and step 4. There is no separate check to run.
- **The run mode** is `scheduled` unless a human message exists in this session, then `manual`; when uncertain, `manual`. Pass it as `fired_via`.

## 1. The page

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:load_config"}'
```

`found: false`: say "No Command Room Hub is set up for this folder; say `set up my hub` in a Command Room chat." and STOP. Load the page's database tool (`ArtifactData`; a listed deferred tool counts as present) and use the answer's `url` for every read and write. With no such tool, say "The Hub page could not be reached from this run." and STOP: the choices live on the page.

## 2. What waits: three reads, in one turn

`query` on `choices` with `where: [["applied", "==", false]]`; `query` on `choices` with `where: [["by_page", "==", true]]`; `get` on `status/apply` (a document that does not exist reads as null).

## 3. The first call

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"fired_via": "<scheduled or manual>", "page": [<the by_page documents as read>], "status": <status/apply as read, or null>, "waiting": [<the applied == false documents as read>], "workspace_root": "<WS>"}, "name": "cockpit_helpers:apply_start"}'
```

`stop: true`: nothing waits and the run's receipt is written. `set` its `light` as it came back (a press on the page set the light running), then the whole turn is the answer's `line`; STOP. Otherwise send its `writes` as ONE `batch`. A batch refused because a document changed (another run claimed it, or the owner pressed Undo): step 2 again, then this call again with `"run_id": "<the run_id it answered>"` added to its args, once.

## 4. The second call

Step 2's three reads again (the claim changed the documents), then:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"fired_via": "<scheduled or manual>", "n_more": <apply_start's n_more>, "page": [<the by_page documents as read>], "run_id": "<apply_start's run_id>", "status": <status/apply as read, or null>, "waiting": [<the applied == false documents as read>], "workspace_root": "<WS>"}, "name": "cockpit_helpers:apply_run"}'
```

Send each list in its `batches` as ONE `batch`, in order, then `set` its `light` as it came back (none when it is null). A batch or the light refused because a document changed: step 2's reads again, then

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"docs": [<both choices queries as read>], "status": <status/apply as read, or null>, "workspace_root": "<WS>", "writes": [<the refused batch, or the light, as apply_run answered it>]}, "name": "cockpit_helpers:apply_repin"}'
```

and send its `writes` as one `batch`, once. A second refusal is left alone and named after the line.

## When a call says no

An answer with `ok: false` from either call, or a call that fails: one call, then `set` its `light` as it came back, say its `line`, STOP. The light never stays on running.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"error": "<the line of that answer, or the reason the call failed>", "fired_via": "<scheduled or manual>", "prior": <the counts apply_run answered, or null>, "run_id": "<the run_id apply_start answered, or empty>", "status": <status/apply as read, or null>, "workspace_root": "<WS>"}, "name": "cockpit_helpers:apply_finish"}'
```

## 5. The end

`next: done`: the final response is the answer's `line`, one line, and nothing else. `next: detail`: a mail send or draft (`connectors.inbox`), a "Missed something?" search (`connectors.miss`) or a feed to refresh (`feeds`) is left; read `cockpit-apply-detail.md` and do only what it says for those, then its light and receipt.
