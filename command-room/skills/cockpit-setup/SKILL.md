---
name: cockpit-setup
surfaces: both
description: "Sets up the owner's Command Room Hub (formerly the cockpit). Fires on: 'set up my hub', 'rebuild my hub', 'update my hub', 'where is my hub', and the older 'set up my cockpit', 'rebuild my cockpit', 'update my cockpit', 'where is my cockpit'. One private page with a Start button that switches every scheduled task back on, preps the next meetings, drafts the inbox, shows the plate and sends the page's choices to the book. Publishes the page, registers the four Hub tasks, reconfirms Morning Brief and Maintenance, and records the page address in the folder. Does NOT fire on 'brief me on today' (morning-briefing), 'triage my inbox' (inbox-triage), 'show my day', or 'set up command room schedules' (enable-command-room-schedules)."
---

# cockpit-setup

One page, four tasks, one address (COCKPIT1, cr1#608; four tasks per
cr1#619). The page lives on claude.ai as a private artifact with its own
small database; the tasks are `cockpit-prep`, `cockpit-inbox`,
`cockpit-apply` and `cockpit-refresh`, each with its orchestrator in
`references/orchestrator-cockpit-<leg>.md` and the shared steps in
`references/cockpit-shared.md`; the address is kept in
`_hq/config/cockpit.json`. The page never writes the book. Its choices wait
in its database until a run of the task applies them through the same
writers the chat widgets use.

The page is called **Command Room Hub** (renamed from "cockpit" on the owner's
ruling, cr1#670; the skill, its files and task ids keep the old word, and
the owner never sees them). Three phrases, one path: `set up my hub` does
everything below; `rebuild my hub` and `update my hub` do the same but publish
to the SAME address (the stored one) and re-register the task bodies; `where
is my hub` answers Step 1's address and stops. The same phrases with
`cockpit` in place of `hub` still work. Every line this skill says names it
the Hub, never the cockpit.

## Writer Contract

Read `shared/WORKSPACE_API.md` first. This skill writes the folder only
through the door: `cockpit_helpers:save_config` (the page address), the
registration writer (`cockpit_helpers:land_registration`, which lands through
the `schedule_backend:record_registration` that `enable-command-room-schedules`
names) and, for a refreshed task,
`schedule_refresh:log_schedule_refreshed`. It writes the page's database
through the artifact database tool, never the book.

## Step 0: Run the Access preamble

Run the Access preamble first (CONTRACT Rule 22 v6, the block in
`shared/WORKSPACE_ACCESS.md`): it resolves `$PLUGIN_ROOT`, exports `CR_ENV`
and finds the workspace. Every access-layer line below is rendered with
`workspace_access.py plan` on the merged seat and run here on every other
seat, exactly as the preamble says.

## Step 1: One account, end to end

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>", "writer_pair": ["<the CR_WRITER_ID this run's rendered lines carry>", "<the CR_WRITER_DERIVATION they carry>"]}, "name": "cockpit_helpers:setup_check"}'
```

**The pair (cr1#646, the update bridge's own rule, BRIDGE2).** `writer_pair`
is the pair this run's rendered lines already carry in front of `python3`:
pass it, as the two-item list shown, here and in Step 2's planning call.
When no rendered line carries one, pass `null`: the check then takes the
pair the door forwards on this very line (`CR_WRITER_ID` /
`CR_WRITER_DERIVATION`) before it ever answers that the chat has no
account. A check run on the customer's computer has no account of its own;
without the pair it refused a seat that can register.

`ok: false` means stop: say the answer's `line` as the whole turn. It refuses
a chat with no account of its own (a cockpit needs the cloud scheduler) and a
chat under a different account from the workspace's declared scheduled
writer (the page would fire a task that refuses every write). `ok: true`
carries `writer_id` and `basename`.

Then read the stored address:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "cockpit_helpers:load_config"}'
```

On `where is my hub` (or `where is my cockpit`): say the `url` (or "No Command
Room Hub is set up for this folder yet; say `set up my hub`.") and STOP.

## Step 2: Register the four cockpit tasks, Morning Brief and Maintenance

Find the scheduler tools the way `enable-command-room-schedules` does (its
scheduler discovery step, verbatim) and read the account's trigger listing
once, every page, the way that skill's Step 1.B does. Pass each row as
`{trigger_id, name, cron_expression, enabled}` in `registered`, and nothing
else from the row: the door refuses a computer path, and a prompt is too long
for this line (cr1#733). The planner places each task by its exact name, or a
renamed chat by the trigger id in the stored map, so no normalising call is
needed. Then plan all six in one call (the four cockpit tasks, then Morning Brief and Maintenance):

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"discover_block": "<the discovery block, as the schedules skill obtains it>", "plugin_version": "<the installed plugin version>", "registered": [<the listing rows, those four fields>], "tools": [<this session's tool names>], "tz_name": "<the workspace timezone>", "workspace_root": "<WS>", "writer_pair": ["<the CR_WRITER_ID this run's rendered lines carry>", "<the CR_WRITER_DERIVATION they carry>"]}, "name": "cockpit_helpers:plan_cockpit_registration"}'
```

The line carries no folder path (cr1#732): the door refuses a computer path as an argument. On a merged seat the folder the tasks register is the `CR_DEVICE_WORKSPACE` the door forwards, so render the line with that export in the same command; with none forwarded, nothing is planned. On a seat whose files are local the planner uses the workspace itself.

`ok: false` ends the turn with its `line` (no folder forwarded, no scheduler, the older desktop
app's scheduler, no account, or a cron the backend refuses). Otherwise, for
EACH of the six `plans`, in order, exactly one of three: `create` or `update`
(execute its `tool` with its `args`, then land its `landing`), or `current:
true` (nothing to call). **`landing` is the one landing path for both**
(cr1#734): the door payload the plan already composed, rendered with `plan
run_writer` and sent as it came, in ONE call. A create's names
`cockpit_helpers:land_registration` and its `trigger_id` is the one value you
fill in, with the id the call returned; it refuses an unfilled one, and that
refusal is `result.ok: false` inside an envelope that says `ok: true`, so read
the result and land it again with the id. An
update's names
`schedule_refresh:log_schedule_refreshed`, one receipt per refreshed task, and
needs nothing filled in. Keep each trigger id. The four are
`cockpit-prep` (6:45 AM weekdays), `cockpit-inbox` (7:15 AM weekdays),
`cockpit-apply` (hourly, 8 AM to 3 PM, the one writer from the page to the
book; the ruling asked for every half hour, and the cloud scheduler refuses
fires closer than an hour apart, so hourly is the nearest it takes) and `cockpit-refresh` (half past, 7 AM to 3 PM; the cloud scheduler
refuses an hour range that crosses midnight in UTC, so the afternoon stops
at three); each runs its one leg
and writes its one light. `stale` names the one-task `Cockpit` trigger of an
earlier build, and the four tasks under their names before the Hub rename
(`Cockpit Prep`, `Cockpit Inbox`, `Cockpit Apply`, `Cockpit Refresh`), when
the listing still carries them: **delete each** with its `delete` call, exactly
as planned, and say so in one clause (cr1#676: a task only switched off was
switched back on by the page's Start, so every leg ran twice). An entry with
no `delete` (a backend that cannot delete), or a delete that is refused, is
switched off with its `switch_off` call instead, and the clause says it is
off and can be deleted from the Scheduled list. The update bridge never
deletes these: their task ids are the Hub's own.

Plans 5 and 6 are Morning Brief and Maintenance (`task_id` `morning-brief`
and `maintenance`), the two chats of the previous version that survive the
cockpit. They are handled exactly like the four above: a `create` or an
`update` is executed and its `landing` landed, a `current` needs no call. One already in the listing with no prompt to compare is `current` with
`not_compared: true`: its text is unknown, so it is never rewritten and never
created again. **Never stop after the fourth plan.** The first build asked for these
two in a separate call, and a real set-up skipped it, leaving a seat with no
Morning Brief and no Maintenance (cr1#631). A chat listed in
`chats_refused` is named in the final line with its `line`; it never stops
the cockpit.

The final line (Step 6) names what was created, refreshed and left
alone across all six. None of the cockpit tasks is a daily chat: the
schedules skill never registers them, and `change-schedule` does not list
them.

## Step 3: Render the page

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"basename": "<basename from Step 1>", "plugin_version": "<the installed plugin version>", "published_at": "<now ISO>", "server": "Claude Code Remote", "workspace_root": "<WS>"}, "name": "cockpit_helpers:render_page_file"}'
```

`server` is always the connector's display name, `Claude Code Remote`, with
spaces (cr1#636): the scheduler listing and the tool prefix spell it
`Claude_Code_Remote`, and a page declared under that spelling reaches no
connector, so every button stays off. The helper maps an underscored name
to the display name anyway, but pass the display name.

The answer's `html` is the page, filled and leak-scanned, and
`capabilities` is the declaration it needs. **Never edit the html and never
type a capabilities object by hand.** Save the html to a file in this
session's own scratch (never in the workspace).

## Step 4: Publish, private, at the same address

Publish that file with the Artifact tool as a default-private page, title
`Command Room Hub`, icon `dashboard`, passing `capabilities` exactly as the answer
returned it. On `rebuild` or `update`, pass the stored `url` from Step 1 so
the address does not change; on a first set-up, omit it. Never share, widen
or publicize the page: it shows the owner's own people and work.

Then record the address:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"basename": "<basename>", "plugin_version": "<the installed plugin version>", "published_at": "<now ISO>", "server": "<the server name passed in Step 3>", "trigger_id": "<the cockpit-apply trigger id>", "url": "<the published page address>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:save_config"}'
```

## Step 5: Seed the page's database

Load the artifact database tool (`ArtifactData`; a listed deferred tool is
loaded, never read as absent). With the page's `url`:

- `set` on `config/page`: `{"basename", "server", "plugin_version",
  "published_at", "trigger_ids": {"cockpit-prep", "cockpit-inbox",
  "cockpit-apply", "cockpit-refresh"}}` with the Step 2 and Step 3 values;
- `set` on `status/<leg>` for each of `prep`, `inbox`, `apply`, `refresh`,
  the document from one `cockpit_helpers:status_doc` call each with
  `state: "idle"` and `line: "Not run yet."`.

Then one functional check: `list` the `status` collection and confirm the
four documents are there. If the tool is absent in this chat, say so in the
final line; the page still works and its first run seeds the lights.

## Step 6: Say where it is, then STOP

One line: the page address, what Step 2 created, refreshed and left alone
(six triggers), and "Open it on claude.ai, allow the Claude Code Remote
connector when the page asks, and press Start." When the answer from Step 3
carried `mail_direct`, the line also says the page asks once to use the mail
connector the first time Send is pressed. On a first set-up, also fire
`cockpit-refresh` once now (the scheduler's fire call on its trigger id and
NO text: a fire that carries text starts without the computer's device tools,
cr1#651) so the page is filled when the owner opens
it. Nothing else: no description of the tabs, no
summary of what was registered.

## What the page does and does not do

- **It never writes the book.** Done, Add context, Confirm, Snooze, Send,
  Draft, Reopen and a press on the Review or Week tab are saved as choices in the page's database;
  `cockpit-apply`, the one writer from the page to the book, sends them
  through `cockpit_helpers:apply_commitment_choices`,
  `cockpit_helpers:apply_review_choices`, `cockpit_helpers:apply_week_choices`
  and apply-choices' own `cr-inbox` handlers. The three producers never read a choice. Send needs a second press on
  the page, which mints a one-use token the run checks.
- **Send and Draft go out from the page itself** (cr1#710 and cr1#727, owner
  rulings 2026-11-04), through the seat's declared mail connector: Superhuman,
  Gmail or Microsoft 365 (`mail_direct` in `cockpit_helpers.py` picks the connector
  and its tools; `render_page_file` declares them). The page re-reads the
  thread first and stops on a newer message, and writes the result on the row.
  Send keeps its second press, and is recorded as sending before anything goes
  out. Draft makes the reply in the thread and sends nothing. A seat with no
  such connector, or a thread the page cannot read, keeps both on
  `cockpit-apply` exactly as before; so does Draft on Gmail, whose plan has no
  draft tool. Snooze and every book write still wait for `cockpit-apply`, and
  a choice older than seven days expires instead of applying.
- **It never republishes itself.** A task writes feeds; only this skill
  republishes the page. Start fires prep, inbox and apply at once and
  refresh once apply is done, so the four lights fill side by side.
- **Its data lives on claude.ai**, readable by the owner alone under the
  page's rules: ids, titles, drafts, status lines and times, and the short
  source line of a commitment (capped, from its own record, cr1#725), never
  original mail text beyond that line. A written data-custody answer precedes any client set-up.

## The Access preamble this file refers to

Propagated by `scripts/dev/propagate_access_preamble.py`; the canonical copy is in `shared/WORKSPACE_ACCESS.md`.

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
