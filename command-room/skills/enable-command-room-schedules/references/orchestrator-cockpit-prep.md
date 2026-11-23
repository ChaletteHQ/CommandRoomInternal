# Cockpit prep (taskId: cockpit-prep)

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this run preps the owner's next meetings and writes the cockpit page's Today feed. It writes the book only through call-prep's own writers and the feed through the artifact database tool. It posts NO widget and lands NO page of its own: the chat turn is one line, then STOP. The page is the surface; this run is one of its four engines (COCKPIT1, cr1#608; four tasks per cr1#619).

Read `cockpit-shared.md` in this folder first and do its shared step 0 and shared step 1 with leg `prep`. Then the whole run in one call (the next section). The leg after it is the same work in steps, for a run where the one call is refused; then the shared last step.

## The whole run in one call (cr1#1603)

This section IS the prep run when its one call is answered. The leg below describes the same work one step at a time; it is used only when the one call here is refused (the last paragraph of this section says when). The reads a script cannot make stay yours: the two calendar reads and call-prep's own run for each meeting. Everything after call-prep, the leg's result and the Today feed, is built in one process beside the data, with the record log read once, and pulled back in sized parts.

**1. The two calendar reads,** exactly as the leg below describes them, nothing added and nothing dropped: the next four meetings of the next 24 hours, with morning-brief Step A's filters (each `{"meeting_id", "title", "time_label", "start"}`), and the fourteen days from seven days before the start of today (the last seven days for Past meetings, then the seven days from today), with the Today feed's own rules (every event with its `others` and, where the calendar says them, `event_id`, `link`, `attendees`, `organiser`, `recurring`, `answer` and `tz`; each `title` exactly as the connector returned it).

**2. Morning-brief Step B and the first half of Step C,** exactly as the leg below says: the `auto_fire` knob, `morning_brief_helpers:prep_leg_plan` over the four meetings, then `skills/call-prep/SKILL.md` end to end for every meeting in its `generate` (with `generated_by="morning-brief"` and `fired_via` from shared step 0). Keep the plan's `started_at` and what each call-prep run returned.

**3. One writer call** (the leg's result and the Today feed, together):

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"device_workspace": "<WORKSPACE_ABSOLUTE_PATH, when known>", "generated": {"<meeting_id>": <the dict call-prep returned, null for a deliberate skip, or {"error": "<why>"}>}, "meetings": [<the four meetings, as handed to the plan>], "now_iso": "<now ISO>", "run_id": "<this session id>", "started_at": "<the plan's started_at>", "week": [<every event of the fourteen days of step 1's second read, the last seven and the next seven, each {"meeting_id", "title", "start", "end", "calendar", "place", "others"} with the read's other fields>], "workspace_root": "<WS>"}, "name": "cockpit_helpers:prep_all"}'
```

When the calendar read itself failed, add `"discover_error": "<what failed, in plain words>"` in its sorted place: the leg degrades to its one banner, as Step C's second half does. The answer is small and carries no feed: `leg` (the leg's result, the same one `prep_leg_result` returns), `token` and `parts` (what to pull next), `pages` (the document, whether it was built, its size and its seconds), `failed`, and `light` and `line` (the finish).

**4. Pull each part, one call per part,** numbered from 0 to one less than the length of `parts`:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"part": <the part's number>, "task": "prep", "token": "<the token>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:refresh_part"}'
```

A part must reach a file in this session's own scratch WHOLE, exactly as the refresh step says (`orchestrator-cockpit-refresh.md`, its step 3): never retyped, shortened or re-composed, and never saved in the workspace.

**5. Unpack the parts HERE, in this shell,** and send the batch, exactly as the refresh step's step 4 says, with `--versions` pointing at a `list` of `feed` saved with `out_dir` in this session's scratch and no `--pin`:

```bash
python3 "$RT/shared/scripts/cockpit_helpers.py" unpack-refresh --out "<scratch>/out" --token "<the token>" --parts <how many parts> --versions "<scratch>/page" "<each saved part file>"
```

It answers `ok: false` with `missing` when a part did not arrive whole: pull that part again and run this again; send nothing until it answers ok. Then send each batch as ONE ArtifactData `batch`, exactly as printed.

**6. The finish.** Send the batch whenever `parts` has a part, even when the answer's `light` is an error: a leg that degraded (a failed calendar read) still built its Today feed, and only the light carries the error. When `parts` is empty (`ok` false), nothing was kept and nothing is sent. When the batch landed, or there was none to send, `set` the answer's `light` on `status/prep`, pinned to the version the running write returned. When a batch could not be sent, write the light through shared step 1's helper as `error` with one sentence saying the Today feed did not land, never the answer's `done` light. Then the shared last step. The final response is the light's sentence.

**When the one call is refused.** An answer that says the name is not on the list (a runtime staged before this call existed), or one with no `token` and no `light`, means the one call cannot run here. Only then, do the leg below in its order: Step C's second half (`morning_brief_helpers:prep_leg_result`) and the Today feed as their own calls. The calendar reads and call-prep's runs are already done: never read or prep twice.

## The leg: the next four meetings

Read the next 24 hours from the business calendar and, where the workspace names one, the personal calendar (the same read morning-brief Step A makes, with its filters: drop passed meetings, drop personal calls, keep internal and external business meetings). Keep at most the next four meetings that have not yet started, as `{"meeting_id", "title", "time_label", "start"}` each. Then run morning-brief Step B and Step C exactly: the `auto_fire` knob, `morning_brief_helpers:prep_leg_plan`, `skills/call-prep/SKILL.md` end to end for every meeting in `generate` (with `generated_by="morning-brief"` and `fired_via` from shared step 0), then `morning_brief_helpers:prep_leg_result`. The leg's own reuse rule skips any meeting already prepped today.

Then write the Today feed from a calendar read of the last seven days and the next seven days, with the same filters (cr1#1017: the Week view on Today shows the seven days; the Today list itself still shows only the next 24 hours, which the feed cuts for it). Pass every meeting of the fourteen days, the last seven and the next seven, not only four. The morning brief's drop rules (personal calls, solo blocks) do NOT apply to this read: pass all-day events and time-away blocks with no attendees too, from both calendars, because the owner's own time away marks its days as away and a work meeting booked on such a day is shown as a clash (cr1#1641: only his own; someone else's out-of-office on his calendar, an event another person organises or one titled with another person's name, marks nothing). With each event pass `others`: how many people besides the owner are on it, `0` for a block with nobody else on it, left out when the calendar does not say (cr1#1293: only an event with another attendee counts as a meeting; a timed `0` reads as hours blocked):

That read starts seven days before the start of today (midnight in the workspace's zone), so the meetings of the last seven days and those that already ended today are in it: the Hub lists them under Past meetings (cr1#1539), each with its state and its wrap-up (cr1#1668). The Week view still shows only the seven days from today; the feed sets the earlier days aside for Past meetings. Where the calendar says them, pass these with each event too, each left out when it does not say (the Hub offers Join, Move and Cancel only on what the read said plainly, and guesses nothing): `event_id`, the event's own id exactly as the calendar connector returned it; `link`, its video or join link; `attendees`, everyone on it as `{"name", "email"}` each, with `"self": true` on the owner's own entry; `organiser`, `true` when the owner organises it and `false` when someone else does; `recurring`, `true` or `false`; `answer`, the owner's own answer (`accepted`, `declined`, `tentative` or `none`); `out_of_office`, `true` when the calendar itself marks the event as an out-of-office event (its own out-of-office type, not a title), left out otherwise; `tz`, the event's own time zone by its IANA name. In the call below they ride in each meeting's object beside the keys shown. Pass each event's `title` exactly as the calendar connector returned it, character for character, never shortened, trimmed or reworded: where the Hub moves a meeting on a connector whose change call takes the title, that title is sent back to the calendar.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"device_workspace": "<WORKSPACE_ABSOLUTE_PATH, when known>", "meetings": [<each {"meeting_id", "title", "start", "end", "calendar", "place", "others"}>], "now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_today"}'
```

`set` the answer on `feed/today` (pinned by the version rule in `cockpit-shared.md`). Light: `done` with "Prepped N, reused M." or `error` with the leg's banner.
