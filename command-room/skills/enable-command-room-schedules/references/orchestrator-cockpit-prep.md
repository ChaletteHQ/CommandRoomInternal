# Cockpit prep (taskId: cockpit-prep)

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this run preps the owner's next meetings and writes the cockpit page's Today feed. It writes the book only through call-prep's own writers and the feed through the artifact database tool. It posts NO widget and lands NO page of its own: the chat turn is one line, then STOP. The page is the surface; this run is one of its four engines (COCKPIT1, cr1#608; four tasks per cr1#619).

Read `cockpit-shared.md` in this folder first and do its shared step 0 and shared step 1 with leg `prep`. Then this leg, then the shared last step.

## The leg: the next four meetings

Read the next 24 hours from the business calendar and, where the workspace names one, the personal calendar (the same read morning-brief Step A makes, with its filters: drop passed meetings, drop personal calls, keep internal and external business meetings). Keep at most the next four meetings that have not yet started, as `{"meeting_id", "title", "time_label", "start"}` each. Then run morning-brief Step B and Step C exactly: the `auto_fire` knob, `morning_brief_helpers:prep_leg_plan`, `skills/call-prep/SKILL.md` end to end for every meeting in `generate` (with `generated_by="morning-brief"` and `fired_via` from shared step 0), then `morning_brief_helpers:prep_leg_result`. The leg's own reuse rule skips any meeting already prepped today.

Then write the Today feed from a calendar read of the next seven days, today first, with the same filters (cr1#1017: the Week view on Today shows the seven days; the Today list itself still shows only the next 24 hours, which the feed cuts for it). Pass every meeting of the seven days, not only four. The morning brief's drop rules (personal calls, solo blocks) do NOT apply to this read: pass all-day events and time-away blocks with no attendees too, from both calendars, because an all-day event that says time away marks its days as away and a business meeting on such a day is shown as a clash:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"device_workspace": "<WORKSPACE_ABSOLUTE_PATH, when known>", "meetings": [<each {"meeting_id", "title", "start", "end", "calendar", "place"}>], "now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_today"}'
```

`set` the answer on `feed/today` (pinned by the version rule in `cockpit-shared.md`). Light: `done` with "Prepped N, reused M." or `error` with the leg's banner.
