# Cockpit refresh (taskId: cockpit-refresh)

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this run writes the cockpit page's plate feed from the book. It writes the book nothing but the plate's display numbers (building the plate mints them), and the feed through the artifact database tool. It posts NO widget and lands NO page of its own: the chat turn is one line, then STOP. The page is the surface; this run is one of its four engines (COCKPIT1, cr1#608; four tasks per cr1#619).

Read `cockpit-shared.md` in this folder first and do its shared step 0. Then settle the lights (below), then shared step 1 with leg `refresh`, then this leg, then the shared last step.

## First: settle the lights a dead run left

`list` the `status` collection and pass every document to the helper; it returns the lights that stayed `running` past the timeout, rewritten as `no_answer` with their line:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"docs": [<every status document, as read>], "now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:settle_statuses"}'
```

`set` each returned document on its own `status/<leg>` (pinned by the version rule in `cockpit-shared.md`). This is the one place a task writes another task's light, and only a light no run has touched for `RUNNING_TIMEOUT_MINUTES`: a run that died must not leave its light running until morning. An empty answer means nothing to settle.

## Then: clear the old choices (cr1#683)

A press stays in the page's `choices` collection after Apply writes it, so the page can show its result and offer Undo; past the expiry window it only slows the page and fills the database. `query` on `choices` with `where: [["applied", "==", true]]` and pass the documents to the helper:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"choices": [<the documents as read>], "now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:plan_prune_choices"}'
```

Send its `writes` as ONE ArtifactData `batch`, exactly as they came back (a delete needs no pin). It never touches a waiting or a claimed choice, and it never changes the light: a refused batch is left for the next run. An empty answer means nothing to clear.

## The leg: the plate

The scheduled fire runs half past the hour from 7 AM to 3 PM so the page is current when the owner opens it; the page fires it after `cockpit-apply` turns green, so the plate it reads carries the closes. Either way the leg is one writer call:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_plate"}'
```

`set` the answer on `feed/plate` (a large answer goes through `file_path`: save it in this session's own scratch first, never in the workspace; pinned by the version rule in `cockpit-shared.md`). The answer carries the header, the focus rows, the chase and schedule rows, the whole plate capped, and the closes of the last 24 hours. Light: `done` with "N on the plate, M closed overnight."

**Then the work streams and priorities (cr1#660, cr1#1013).** One read beside the data, after the plate, so its counts match the closes the plate just showed. It reads the thread, work-stream and priority cards, the open list and the loose-ends list's items on no thread (the Work streams tab's "No home yet" section, cr1#1013 part 3), and feeds both the Work streams tab and the Priorities tab:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_projects"}'
```

`set` the answer on `feed/projects` (the same `file_path` rule for a large answer; pinned by the version rule in `cockpit-shared.md`). It never changes the light: a failed projects read leaves the old feed in place, and the light says what the plate said.

**Then the Week board (cr1#1017).** One read beside the data, after the work streams: the next seven days' due items from the open list and one lane per confirmed priority, for the Week view on Today (its meetings come from the Today feed's `week`, which the prep run writes):

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_board"}'
```

`set` the answer on `feed/board` (the same `file_path` rule for a large answer; pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the Today lists (cr1#662).** The owner's active reminders and the priority to-dos they added on the page, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_mine"}'
```

`set` the answer on `feed/mine` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the mail rules (cr1#1020).** The owner's mail rules in force and the threads dropped from the inbox, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_mail_rules"}'
```

`set` the answer on `feed/mail_rules` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the people (cr1#1020).** Everyone on file with what is open between the owner and them, and who is starred to Home, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_people"}'
```

`set` the answer on `feed/people` (the same `file_path` rule for a large answer; pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the Brief tab (cr1#1310).** Today's morning brief as the Morning Brief run saved it, one read beside the data. It reads that one saved file and nothing else, so the tab shows exactly what the brief posted:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_brief"}'
```

`set` the answer on `feed/brief` (the same `file_path` rule for a large answer; pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then To confirm (cr1#779; the Review tab until cr1#1286).** The open questions and proposed closes, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_review"}'
```

`set` the answer on `feed/review` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the Done tab (cr1#780).** The recap's sections first, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "since_iso": "<seven days before now, ISO>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:week_sections"}'
```

Then hand its answer to the feed, and `set` that answer on `feed/week`:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "sections": <the week_sections answer>, "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_week"}'
```

It is pinned by the version rule in `cockpit-shared.md`. Like the projects, it never changes the light.

**Then the Settings tab (cr1#781).** Everything learned and every setting, each with its source, read from the stores the skills read, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_settings"}'
```

`set` the answer on `feed/settings` (pinned by the version rule in `cockpit-shared.md`). Like the projects, it never changes the light.

**Then the Maintenance record (cr1#666).** When the Maintenance chat last finished, each job's last completion and how many hours it is behind, one read beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:maintenance_status"}'
```

`set` the answer on `status/maintenance` (pinned by the version rule in `cockpit-shared.md`), unless it answers `idle` while the page's document says `done`: that is a read that failed, and the page keeps what it has. It is not one of the four lights: it records when Maintenance last kept the book up, the page shows its line only when a job failed or a connector went unread (cr1#822), and Start runs Maintenance on every press whatever it says (cr1#682). The document names the fire that left the note (`fired_via`, `fired_at`, cr1#743): a scheduled fire's word reaches the page here, at the next Refresh or Start, exactly as a typed run's does. Like the projects, it never changes this task's light.
