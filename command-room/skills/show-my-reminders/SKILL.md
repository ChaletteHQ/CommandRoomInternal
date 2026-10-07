---
name: show-my-reminders
surfaces: both
description: "The user's own pin-until-cleared reminders. Capture: 'remind me about' [X] on/next [day] / 'remind me to' [X] on [day] / 'set a reminder' (every-week/month = repeating). Review: 'show my reminders' / 'my reminders' / 'list my reminders'. Clear: 'done with the reminder' / 'clear the reminder'. Move: 'push the reminder' to [day] ('push it to' follows in context). Pins to the morning brief from its date, daily, until cleared. Does NOT fire on 'show my list' / 'my list' / 'add to my list' (show-my-list, the discuss-later queue), 'remind me to revisit' (decision-log — its revisit mode), 'remind me what' retrieval (decision-log), or 'balance check' / 'my white space' / 'plan a date night' (balance — proactive + relationship-aware, not a reminder dump; it READS personal reminders but never manages them)."
---

# show-my-reminders

Reminders v1 (v4.6.0 W4a). A reminder is the user saying "remind me about X
on [day]" — from that day it renders on the morning brief **every day until
manually cleared or pushed**, like a personal task the brief asks about.
This skill owns the whole lane: capture, the full-list review surface,
clear, push, keep, and repeat.

**Reminders are NOT commitments.** Own event family (`reminder` /
`reminder_updated` / `reminder_cleared` — see `shared/EVENT_TYPES.md`
§ Reminder lane), own lifecycle (pin-until-cleared: never auto-ranked, never
auto-faded, never chased), and they never appear in commitment buckets,
counts, chase, or triage. Recurrence lives here, not on commitments.

## The hard rule — user-minted only

`data.origin` is `user_explicit`, always. **No skill, sweep, scheduled task,
or orchestrator may ever create or mutate a reminder on its own** — not as a
"helpful" capture, not from a transcript, not from an email. The builders in
`shared/scripts/reminders.py` reject any other origin, `event_gate` rejects
it again at append (unconditionally), and the reader ignores anything that
slipped past both. If a meeting produces something that should nag the user,
that's a commitment — the capture pipeline's job, not this lane's.

## Behavior

### Capture — "remind me about X on/next [day]"

Parse three things from the phrase: the **summary** (the X), the **date**
(`on Friday`, `next Tuesday`, `on the 15th`, `tomorrow` — resolve to a
concrete date in workspace TZ via `tz.py`), and an optional **repeat**
("every week" → `weekly`, "every month" → `monthly`, "every 10 days" →
`{"every_days": 10}`). Then write through the canonical helper — never a
hand-rolled append.

Discover the plugin root first (CONTRACT Rule 22) and run python FROM
`$PLUGIN_ROOT`:

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
```

```python
import sys
sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT per the preamble above
from reminders import capture_reminder

ev = capture_reminder(
    "<absolute path to _hq/data/events.jsonl>",
    "<summary>",
    remind_from="<YYYY-MM-DD>",
    due=None,                # only when the phrase carries a real deadline distinct from the pin date
    ref=None,                # commitment/event id when the user points at a tracked item ("the Rio chase")
    repeat=None,             # "weekly" | "monthly" | {"every_days": N}
    person_ids=[...],        # resolved via entity_resolve when the summary names tracked people
    primary_thread_id=None,
)
```

- `personal` defaults automatically: **True when the reminder references no
  tracked business entity** (no ref, no resolved people, no thread), False
  otherwise. A marked-personal or not-personal correction in the capture
  exchange overrides the default either way (it's a flow reply, not a
  routed command).
- When the summary names a tracked commitment ("remind me about the Rio
  chase Friday"), resolve it via `entity_resolve` + the commitment loader and
  set `ref` to its id. The ref is context only — **clearing the reminder
  never closes the commitment, and closing the commitment never clears the
  reminder.** Say so if the user seems to expect otherwise.
- Ack in one plain line: *"Pinned for Friday — I'll keep it on your brief
  from then until you clear it."* (add *"— repeats weekly"* when it repeats).
  No ids, no event names.

### Review — "show my reminders"

```python
from reminders import load_active_reminders
rems = load_active_reminders("<workspace root>", "<today>", surface="m_facing")
```

Render a plain markdown list in three groups, skipping empty groups:

```
Pinned ([N])
📌 [summary] — day [days_pinned+1] [· due [date]] [· repeats weekly] [· about: [ref context]]
📌 **[summary]** — [days_pinned] days now [(this one's been waiting — top of your brief tomorrow)]

Coming up ([N])
· [summary] — [Weekday, Mon DD] [· repeats monthly]

Scheduled ([N])
· [summary] — [Mon DD]
```

- Bold rows are `escalation == "bold"`; `"top"` rows lead the Pinned group
  with the parenthetical nudge.
- Personal reminders render here (this is an owner-facing surface) with no
  special marking — never label a row "personal".
- Close with the verbs, once, in plain English: *"Say 'done with the
  reminder', 'defer it to [day]' ('push it to Friday' works too), or 'keep'
  on any of these."* Done / Later… / Keep are the taxonomy words — the same
  labels any reminder widget renders, so prose and buttons never diverge
  (F-13 P2a).
- Empty state: *"No reminders right now. Say 'remind me about [thing] on
  [day]' and I'll pin it to your morning brief from that day."*

This surface is a **pure read** in v1 — no widget, so no fire-marker either
(done/push/keep arrive as chat phrases). The widget verbs ARE registered
(v4.5.2 S2): `reminder done` / `reminder push [date]` / `reminder keep` in
`shared/scripts/verb_taxonomy.py`, displaying **Done / Later… / Keep**, with
dispatch spec'd in apply-choices § `show-my-reminders` — any future widget
render uses those rows, never ad-hoc verbs.

### Clear / push / keep

Resolve which reminder the user means (the one just rendered, the only
pinned one, or match by summary words — ask when genuinely ambiguous), then:

```python
from reminders import build_reminder_cleared_event, build_reminder_updated_event
from event_gate import append_event

# "done with the reminder" / "clear it"
append_event(events_path, build_reminder_cleared_event(rem_id), holder="show-my-reminders")

# "push it to Friday"
append_event(events_path, build_reminder_updated_event(rem_id, action="push", remind_from="<YYYY-MM-DD>"), holder="show-my-reminders")

# "keep" / "keep it" (acknowledged — resets the escalation clock, stays pinned)
append_event(events_path, build_reminder_updated_event(rem_id, action="keep"), holder="show-my-reminders")
```

- Clearing a **repeating** reminder re-arms it: the reader derives the next
  occurrence from the clear + the repeat rule at render time (there is no
  scheduler and none may be added — derive-next-on-read is the design). Ack
  plainly: *"Cleared — it'll be back Monday."*
- Clearing a reminder with `ref` clears ONLY the reminder. If the user's
  phrasing says the underlying item is also done ("done with the Rio
  chase, clear the reminder too"), close the commitment through
  `commitment_state.close_commitment` as its own action — two events, two
  acks folded into one line.

## Privacy (M choice ④)

`personal: true` reminders render ONLY in owner-facing surfaces — the
morning brief and this skill. They are **excluded from every client-facing
deliverable, team-intelligence output, and export**. The reader enforces the
floor: `active_reminders` / `load_active_reminders` default to
`surface="client_facing"`, which strips personal rows — only the two
owner-facing surfaces pass `surface="m_facing"`. A new surface that never
heard of reminders gets the safe behavior by default.

## Fences

- **`show my list` is a different skill.** show-my-list is the RETIRED
  discuss-later queue (`commitment_to_discuss`, drain-only since MLK1 —
  nothing captures into it anymore). Reminders are date-pinned nags. The
  two never cross-fire and neither reads the other's events. If the user
  says "add it to my list" (old muscle memory), that phrase still routes
  to show-my-list — it explains the retirement; never treat it as a
  reminder capture.
- **Not the commitments lane.** "push [commitment] to [date]" inside the
  Waiting On / My Plate chats is that orchestrator's verb (writes `commitment_updated`);
  "push the reminder" here writes `reminder_updated`. Same word, different
  ledgers — resolve by what's in context.
- **Not decision-log's revisit mode** ("remind me to revisit the pricing decision") and
  not retrieval ("remind me what we decided") — those route to their owners.

## What this skill does NOT do

- Never creates a reminder the user didn't explicitly ask for (the hard rule
  above — there is no proactive-capture path in this lane, by design).
- Does not touch connectors (no Gmail / Calendar / Granola fetches).
- Does not write or close commitments, except the user-directed
  `close_commitment` companion call described under Clear.
- Does not schedule anything — no scheduled task, no cron; the morning brief
  derives what's active when it renders (Step 3f of morning-briefing).

## See also

- `shared/scripts/reminders.py` — builders + the pure reader (the ONLY
  read/write path for this lane).
- morning-briefing Step 3f — the Pinned / Upcoming sections + 3d/7d
  escalation rendering.
- show-my-list — the discuss-later queue (fenced above).

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
