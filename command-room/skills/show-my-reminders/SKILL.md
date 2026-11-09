---
name: show-my-reminders
surfaces: both
description: "The user's own pin-until-cleared reminders. Capture: 'remind me about' [X] on/next [day] / 'remind me to' [X] on [day] / 'set a reminder' (every-week/month = repeating). Review: 'show my reminders' / 'my reminders' / 'list my reminders'. Clear: 'done with the reminder' / 'clear the reminder'. Move: 'push the reminder' to [day] ('push it to' follows in context). Pins to the morning brief from its date, daily, until cleared. Also the mute ledger: 'show muted' / 'show snoozed' list every live mute with an Unmute. 'show my list' / 'my list' hear one line: that list is retired. Does NOT fire on 'remind me to revisit' (decision-log — its revisit mode) or 'remind me what' retrieval (decision-log)."
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
#    customer opens. Then hand that answer to `workspace_access.py discover
#    --compare '<its JSON>'` in this shell: `runtime_lags: true` (the runtime
#    is absent, or its manifest_sha is not this plugin's -- the version alone
#    no longer changes between builds) is a STOP: run the update-bridge
#    install step once, resolve and compare again, and if it still lags say
#    `lags_line` and stop. There is no container fallback.
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
#  9 FOLDER. Three tiers, one wording here and in the block the customer
#    pastes into the account's Claude instructions (claude_instructions.py,
#    TIER_LINES). If the owner's computer is connected, work in the Command
#    Room folder; if it is not attached, ask for it by name before doing any
#    work. If the owner's computer is not connected, ask for the Command Room
#    folder to be attached from OneDrive or Google Drive before doing any
#    work. If neither is possible, answer as plain Claude and do not go
#    through Command Room. A scheduled chat asks nothing: it works in the
#    folder it was registered with, or stops in one sentence.
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

## The retired discuss-later list: `show my list` / `my list` (cr1#552)

The discuss-later list (`commitment_to_discuss`, drain-only since MLK1) is
retired with its skill, `show-my-list`, on the owner's ruling of 2026-11-06.
Whatever was still open on a seat was moved by the update bridge's
`discuss_list_retired_v1` migration: each item became a note on its person or
project, and an item with no person or project became a My Plate task. So
every phrase of the old family, `show my list`, `whats on my list`, `what's
on my list`, `what to discuss`, `what do i need to discuss`, `discuss list`,
`show discuss list`, `my list`, `add to my list` (the retired capture verb), gets ONE sentence,
verbatim from `retired_skill_lines.DISCUSS_LIST_RETIRED`, and nothing else:
no list render, no read of the ledger, no reminder capture ("add it to my
list" is old muscle memory, not a reminder).

> *"Your discuss-later list is retired. Anything still on it became a note on its person or project, or a task on My Plate, the last time Command Room updated. Say `what's on my plate` for your tasks, or `show my reminders` for your reminders."*

## Mute ledger mode: `show muted` / `show snoozed` (v4.6.0 S4; here since cr1#552)

The SECOND surface this skill renders: every live mute, so a timed mute
stops being a one-way door. It moved here from the retired show-my-list
because this is the other personal lane. On "show muted" / "show snoozed" /
"what's muted" / "what did I snooze":

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "plate_helpers:live_mutes"}'
```

The answer's `mutes` are `mute_ledger.live_mutes(events, <now>)` over the
owner's own ledger read (the helper's, never a read in this chat): every
live mute, oldest first. Build the data view with `"widget_mode": "all_batch_widget"` and
`"source_skill": "show-my-reminders"`, one row per ledger row, oldest first:
what was muted (the row's `note`, else its `target_id` resolved to the
referenced item's title, else the dismissal's `reason`), which chat it came
from (`surface`), and the remaining time VERBATIM from `ttl_label` ("3 days
left", every mute states its duration, the F-59 rule). Actions per row:
`unmute` (**Unmute**) · `skip` (**Snooze (1 day)**, hides the ledger row for
a day, never touches the underlying mute). Embed the row's dismissal `seq`
verbatim (widget identity contract).

Render where the data is and relay the page byte-exact:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"data_view": <the data view built above>, "name_hint": "show-my-reminders", "wrapper": "fragment", "workspace_root": "<WS>"}, "name": "plate_helpers:render_list_page"}'
```

**Load the widget tool before anything else** (`shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first): on the merged and half-merged seats it is deferred, and a listed deferred tool counts as present; ToolSearch loads it, its setup tool runs first, then the relay. The answer is `{html, page_rel, bytes_len,
pending_rows}`: land the page with `plan write` at `page_rel`, append every
row in `pending_rows` with `plan append_jsonl` (holder `show-my-reminders`),
then hand `html` to `mcp__visualize__show_widget` as `widget_code`, verbatim.
Never hand-compose or post-process the HTML.

Then the fire-marker apply-choices reads to tell which surface a click came
from (`log_pack_run`, `kind="list"`, `fired_via="manual"`), held beside the
data and landed by ONE append:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"duration_ms": <elapsed_ms>, "fired_via": "manual", "kind": "list", "surfaced": <n_mutes>, "workspace_root": "<WS>"}, "name": "plate_helpers:plan_list_marker"}'
```

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "show-my-reminders", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the marker answer above, in order>]}'
```

Dispatch: apply-choices § show-my-reminders mute-ledger rows (`unmute` → the
ledger's clear writer; the ack says the item re-surfaces on its next chat).
0 live mutes → plain English: `Nothing is muted right now.`, no widget, no
fire-marker.

Fences: permanent never-track rules are NOT in this ledger (they are
suppression rules in the workspace config, lifted by editing the rules
file; when any exist, add ONE footer line with their count); learned
suppressions (surface-preferences) are a separate durable layer, also not
timed mutes.

## Fences

- **The discuss-later list is retired** (above). Its phrases say so in one
  line; nothing captures into it and nothing renders it. Reminders are
  date-pinned nags and stay their own lane.
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
- `retired_skill_lines.DISCUSS_LIST_RETIRED`: the one sentence for the
  retired list's phrases; `plate_helpers.live_mutes` / `render_list_page` /
  `plan_list_marker`: the mute ledger's read, render and fire-marker.

## Routing (full trigger corpus)

The description is budget-capped (G11); the families below are binding at
fire time and enforced mechanically by tests/triggers.yaml.

> Also fires on: 'show muted', 'show snoozed', 'what's muted', 'what did I snooze': the mute-ledger mode, every live snooze/mute with its remaining time and an Unmute action. Does NOT fire on 'never track' rule changes (those are permanent suppression rules, not timed mutes).

> Also answers the retired discuss-later list's phrases with one sentence (cr1#552): 'show my list', 'whats on my list', "what's on my list", 'what to discuss', 'what do i need to discuss', 'discuss list', 'show discuss list', 'my list', 'add to my list'. Does NOT fire on 'what's on my plate' (commitment-triage) or 'show waiting' (the Waiting On surface).

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
