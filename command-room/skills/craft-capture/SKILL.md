---
name: craft-capture
surfaces: both
description: "Captures how a person, a business or you do a kind of work, as a craft file of rules with reasons and evidence. Fires on: 'capture how [name] does [scope]', 'capture how [name] works', 'capture how I decide' (or review, write, run a meeting), 'capture my craft', 'lock this in' (the subject comes from the session), 'remember how [name] works'. Gathers what the workspace holds about them (meetings, documents in their folder, session notes, person facts; mail only when you name it), lists it before reading, previews each rule with its evidence for you to confirm, edit or dismiss, asks at most five questions, tests the confirmed rules on one held-out document or meeting, and writes the file. Capturing again amends the same file. Does NOT fire on 'remember [fact] about [name]', 'who is [name]' or 'how does [name] work' (people-crm), or 'research [name]' (research)."
---

# craft-capture

A craft file is how a person, a business or the owner does one kind of work
(review, decide, write, run a meeting, win business, follow up, prioritize,
delegate, or a kind the owner added), as
numbered rules in the shape "when this, they do that, because", each pointing
at the evidence it came from (SPEC CRAFT1 section 1, `shared/scripts/craft_contract.py`).
This skill makes one from what the workspace already holds, and proves it on
one artifact it held back before it is trusted (SPEC CRAFT1 step 3, cr1#398).
Recall is not this skill's job: once the file is confirmed, the drafting and
prep skills read it on their own (cr1#397), and people-crm lists it on
`what do I know about how [name] works`.

Rulings this skill follows (owner, 2026-10-24 unless dated otherwise):
- It runs only when the owner asks. Nothing is written until the owner has
  confirmed at least one rule.
- Mail is read only when the owner names it: "and my mail", or the threads.
- At most five clarifying questions. Unasked or unanswered ones are kept in
  the file's Open questions, never a reason to stop.
- The kinds of work come from a fixed list the owner can extend.
- A file about someone outside the owner's company carries a consent flag.
  It is a note, never a block.
- Every rule points back at its source (ruling C3).
- Nothing goes into an always-loaded file (ruling A5): this skill writes the
  craft file, one record of the capture and the list of craft files, and
  nothing else.
- A craft file holds method, never a figure (ruling B4, 2026-11-04). The
  check on saving refuses figures, names and every tracked business and
  project; a matter's facts in plain words are not machine-detected, so the
  preview tells the owner they are the check (ruling of 2026-11-11 on cr1#396).

**Personal sources, before the first connector read (cr1#1179).** Render `cd "$PLUGIN_ROOT" && python3 shared/scripts/workspace_access.py plan run_helper --json '{"name": "connector_reads:chat_wall", "args": {"workspace_root": "<WS>"}}'`, paste what it prints and run that, once, before any connector is read here, and follow `shared/CHAT_WALL.md`: call no tool on a server in `connectors`, show no message from a mailbox in `mailboxes` unless its sender is in that entry's `except_senders`, write nothing from either, and say nothing about what was left out.

## Writer Contract

Read `shared/WORKSPACE_API.md` first.

- **Reads:** `_hq/data/entities.json` and the `meeting` and
  `person_fact_observed` rows of `_hq/data/events.jsonl`, through the helper;
  the documents and session notes in the subject's folder; meeting transcripts
  through the transcript connector (the reader `transcript-search` uses); mail
  threads only when the owner names them.
- **Writes:** the craft file at `<subject folder>/craft/<scope>.md` through
  `craft_capture:write_craft` on the write door, and nothing else by hand.
  That writer appends ONE `craft_captured` row and regenerates
  `_hq/views/CRAFT.md`. A new kind of work goes on the list through the
  same writer, with its first saved rule and where it applies, on the
  owner's word only. Moving sell files to win business is
  `craft_capture:move_renamed_scope_files`, on the owner's word. The confirm
  page and the test page land only their audit pages, under
  `_hq/.system/widgets/`, through `craft_confirm:render_confirm_page` and
  `craft_confirm:render_test_page` (cr1#1649).
- **Never writes:** a craft file, an event or a view during the gather, the
  preview or either page; `CLAUDE.md` or any always-loaded file.

## Step 0: Run the Access preamble

Run the Access preamble first (CONTRACT Rule 22 v6, the block in
`shared/WORKSPACE_ACCESS.md`): it resolves `$PLUGIN_ROOT`, exports `CR_ENV`
and finds the workspace. Every access-layer line below is rendered with
`workspace_access.py plan` on the merged seat and run here on every other
seat, exactly as the preamble says.

## Step 1: Whose craft, and which kind of work

Ask the matcher, with the owner's words as typed:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"text": "<what the owner typed>", "workspace_root": "<WS>"}, "name": "craft_capture:match_capture_ask"}'
```

- `result.verb` null: this is not a capture. A person fact ("remember Sam
  prefers Signal") is people-crm, and so are `who is [name]` and `how does
  [name] work`; `research [name]` is research.
- `result.infer_subject` true (`lock this in`): the subject is the person, the
  business or the owner this session worked with or for. When that is not
  plain from the session, ask one question naming the candidates.
- `result.subject_ids`: one id is the subject; several get one question
  naming them (Gate 1's rule); none means the name is not on file: say so in
  one line and stop.
- `result.scope` null: ask one question with the kinds of work on the list
  (`result.scope_text` is what the owner said). When the owner names a kind
  that is not on the list and says to add it, ask one more question: where
  it applies (which skills read it, from `readers` on the refusal or the
  core list: drafts, memos, decisions, call prep, meeting notes and so on)
  and which core kind it sits under, if any. Then gather with `new_scope`
  set to `{"label": "<the kind, in the owner words>", "readers": [...],
  "parent": "<a core kind, or null>"}` and `scope` to its slug (cr1#1391).
  It is added only when its first confirmed rule is saved, never before, so
  a kind of work never exists without a cited rule.

## Step 2: Gather, and list it before reading anything

First collect the pointers the helper cannot see, without reading content:
- the meetings where the subject spoke on this kind of work, from the
  transcript connector, as `{"ref": "<the connector's meeting id>", "date":
  "<YYYY-MM-DD>", "title": "<the meeting title>"}` (the meetings already in
  the record are found by the helper);
- mail, ONLY when the owner named it in this conversation: the named threads
  as `{"ref": "mail:<thread id>", "date": "<YYYY-MM-DD>", "title": "<the
  subject line>"}`, and `mail_named` true. Otherwise `mail_named` stays false
  and no mail is fetched;
- files the owner dropped in or pasted: `{"path": "<path inside the
  folder>"}`, or `{"ref": "<a short name>", "date": "<YYYY-MM-DD>"}` for
  pasted text.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"added": [], "mail_items": [], "mail_named": false, "new_scope": null, "scope": "<the scope slug>", "subject_id": "<the subject id>", "transcript_hits": [], "workspace_root": "<WS>"}, "name": "craft_capture:gather_sources"}'
```

`ok` false: say its `line` and stop; on `scope_unknown` ask Step 1's scope
question with `scopes`, on `scope_declaration` ask where it applies, on
`no_single_folder` ask which of `projects` it is for and gather for that
project. On `renamed_scope_file` (a sell file still sits where win business
goes), say the line; on the owner's yes, move the files, then gather again:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"apply": true, "source_skill": "craft-capture", "user_confirmed": true, "workspace_root": "<WS>"}, "name": "craft_capture:move_renamed_scope_files"}'
```

Say its `line`; the move keeps each old file aside and `undo` takes it back.

`ok` true: before reading anything, say one line naming the subject and the
kind of work (`subject_name`, `scope_label`; "a first capture" when `action`
is `create`, "an amendment to the file you have" when it is `amend`, with
`existing.last_captured` when there is one), then each of `lines` as given,
then `held_out_line` as given. When `mail_left_out` is true, add one line:
mail was not read because it was not named. When `added_refused` is not
empty, add one line: those files are outside the folder and were not
included.

## Step 3: Read and extract (your part)

Read `read_items` in the order given: their own work first, then meetings,
notes, facts and mail, because what people do outranks what they say they do.
Open each by its `read` (a path through `plan read`; a meeting through the
transcript connector by its `source_ref`). Never open `held_out`: it is the
test.

Write each rule in the shape "When <situation>, they <action>, because
<reason>." A refusal or red line is "They never <thing>, because <reason>."
and goes in `What they never do`; a step in their usual sequence goes in
`Order of work`. Method, never content: no figures and no digits at all, no
person's name other than the owner's, no business or project name, and no
facts of a matter. Every rule names the `ref` of each item it came from; a
rule you cannot point at an item is left out. Note the questions the material
raised and did not settle.

Write ONE JSON object into this session's own scratch (never under the
workspace): `{"gathered": <the gather answer's result, unchanged>,
"proposals": [{"text": "<the rule>", "section": "Rules", "evidence":
["<ref>", "<ref>"]}], "questions": ["<question>"]}`. On an amendment, a
proposal that changes an existing rule carries `"amends": "R<n>"`.

## Step 4: The preview card

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "args_file": "<your scratch>/craft_preview.json", "name": "craft_capture:build_preview"}'
```

Then show the confirm page (cr1#1649). Write `{"preview": <the preview
answer's result, unchanged>}` into this session's own scratch and render it:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"workspace_root": "<WS>"}, "args_file": "<your scratch>/craft_page.json", "name": "craft_confirm:render_confirm_page"}'
```

**Load the widget tool before anything else** (`shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first): on the merged and half-merged seats it is deferred, and a listed deferred tool counts as present; ToolSearch loads it, its setup tool runs first, then the relay.

Pass its `html` to `show_widget` as `widget_code`, byte-exact: never edit,
restyle or rebuild it. The page shows each rule with its evidence and Keep,
Edit and Dismiss, the notice word for word, an answer box per question, and
one Save; nothing is saved before Save. Then wait. Save sends ONE message
that starts `craft choices: `: that is the owner's answer to this step. Keep
it exactly as it arrived; never read it into your own words. A line
pasted from the page (when the page could not reach the chat, it shows one
to copy) is the typed path: read its keep, dismiss and edit words and its
answers as typed decisions, each quoted text being the owner's own words
written as JSON (a `\"` inside it is a quotation mark, a `\\` a backslash),
never as the page's message.

When this chat cannot show a widget, when the door refuses, or when
`over_budget` is true, use the typed card instead: say `card_lines` as given,
one per line, in order, the `notice` among them word for word. Do not
re-word, shorten or drop the notice: it is the only place the owner is told
that a matter's facts in plain words are theirs to catch. A rule with no
evidence never reached the card, and nothing here adds one back. Then wait
for the owner: confirm, edit or dismiss for each rule, and an answer for any
question they choose to answer.

## Step 5: The held-out test

After at least one rule is kept or edited: when the preview's `held_out` is
not null, open it (its `read`) and apply the rules the owner kept and edited
(on the page: Keep and Edit, in the edited words; never a dismissed rule).

On the page path, write `{"preview": <the preview, unchanged>,
"confirm_reply": "<the Save message, exactly as it arrived>", "test":
{"original": "<the held-out text as read>", "revised": [{"text": "<a piece
of the result>", "rule": "<the key of the rule that changed it, or null>"}]}}`
into this session's own scratch, the result cut into pieces so every change
is its own piece marked with its rule key, and render the test page:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"workspace_root": "<WS>"}, "args_file": "<your scratch>/craft_test.json", "name": "craft_confirm:render_test_page"}'
```

Pass its `html` to `show_widget`, byte-exact. It shows the original and the
result side by side, each change marked, the test question word for word and
yes, partly and no as three buttons. A press sends ONE message that starts
`craft verdict: `; keep it exactly as it arrived. No press leaves the verdict
null.

On the typed path, or when that door refuses or answers `over_budget`, show
the result beside the original as text: the original first under
**Original**, then the result under **With their method**, each change marked
with the rule key that made it. Then ask `test_question` word for word, once.
The answer is the verdict: yes, partly or no; no answer leaves it null.

When `held_out` is null, say `held_out_line` and ask nothing.

## Step 6: Write the file

Write ONE JSON object into this session's own scratch. On the page path:
`{"preview": <the preview answer's result, unchanged>, "confirm_reply": "<the
Save message, exactly as it arrived>", "verdict_reply": "<the test page's
message, exactly as it arrived, or leave the key out>"}`. The writer reads
both messages itself, so what is saved is exactly what was pressed; never
also pass `decisions`, `answers` or `verdict`. On the typed path:
`{"preview": <the preview answer's result, unchanged>, "decisions": {"P1":
"confirm", "P2": "dismiss", "P3": {"edit": "<the owner's words>"}},
"answers": {"Q1": "<the owner's answer>", "Q2": null}, "verdict": "<yes,
partly or no, or null>"}`.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"source_skill": "craft-capture", "workspace_root": "<WS>"}, "args_file": "<your scratch>/craft_write.json", "name": "craft_capture:write_craft"}'
```

`ok` false: nothing was saved. Say `line` as given; when `refused` is
`privacy`, ask the owner to edit or dismiss the rules it names and run this
step again with their words. When `refused` is `already_saved`, the page's
choices were saved already: say `line`, and do not save again. `ok` true: say
`line` as given; it already says when an answer carried a name or a figure
and so stayed an open question. Each other answer is kept in the
file's History in the owner's words. Nothing else is saved and nothing else
is written.

## Never

- Never write before the owner has confirmed at least one rule.
- Never type the page's choices into `decisions`, `answers` or `verdict`:
  the Save message and the test page's message go to the writer as they
  arrived.
- Never fetch or read mail the owner did not name.
- Never open the held-out artifact before the test.
- Never put a rule, or a pointer to the file, into `CLAUDE.md` or any
  always-loaded file.
- Never start a capture on your own: proposals from the nightly sweep are a
  later step and always ask first.

## The activity log is append-only (MANDATORY, CONTRACT Rule 31)

This skill touches `_hq/data`. **The activity log is never rewritten by hand.**
`events.jsonl` and its yearly shards are only ever ADDED to, through the
writers (`event_gate.append_event` / `atomic_write.atomic_append_jsonl`). No
step here, and no turn this skill runs in, may edit, truncate, reorder, delete
lines from, back up and rewrite, or restore that file, and may never instruct
anyone else to.

- A duplicate or malformed line is **quarantined through the cleanup skill's
  existing path**, never deleted (`recover_corruption.py` for malformed lines,
  `seq_health.py --mark` for a duplicate entry number).
- Correcting writes this skill made means **appending a reversal through
  `brain_undo.undo_batch`** with the batch ref the run advertised, a receipt
  and a real `undo`. `undo` after a re-run means exactly that batch, or the
  words "nothing to reverse"; never an improvised drop, an invented supersede,
  or a hand-edited file. A craft file itself is changed by capturing again,
  which amends it and keeps the old text.
- If you believe the file itself must change, **STOP and say so in plain
  words.** Do not do it, and do not offer to.

## Routing (full trigger corpus)

Fires on (positive):

- 'capture how [name] does [scope]', 'capture how', 'capture how Sam Sample
  does review', 'capture how [name] works', 'capture how Sam Sample works'
- 'remember how [name] works', 'remember how Sam Sample works', 'remember how
  Mira works'
- 'capture how I decide', 'capture how I review', 'capture how I write',
  'capture how I run a meeting', 'capture how I run a session' (the run a
  meeting scope): the owner's own craft
- 'capture my craft' (the owner's own craft; the kind of work is asked)
- 'lock this in' (after a session; the subject is the one the session worked
  with or for)

Does NOT fire on 'remember Sam prefers Signal' (people-crm: a person fact), 'who is Sam Sample' (people-crm), 'how does Sam Sample work' or 'what do I know about how Sam Sample works' (people-crm: reads the craft files), 'research Sam Sample' (research).

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
#    customer opens. Put CR_DEVICE_NAME="<the device name get_device_info
#    reports, spelled as it reports it>" in front of that first plan, in the
#    same command, so each write names the computer it came from (plan keeps
#    it for the session). Then hand that answer to `workspace_access.py
#    discover --compare '<its JSON>'` in this shell: `runtime_lags: true` (the
#    runtime is absent, or its manifest_sha is not this plugin's -- the version
#    alone no longer changes between builds) is a STOP: run the update-bridge
#    install step once, resolve and compare again, and if it still lags say
#    `lags_line` and stop. There is no container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#    CONTEXT, always, one call: `plan run_helper` with name
#    `context_loader:load_context` and args {"workspace_root": "<WS>",
#    "skill": "<this skill's folder name>"}. Follow every rule in its
#    `rules`; an answer a rule shaped says so in the rule's own plain words.
#    Asked about the owner's own records and nothing on file answers, say
#    "I do not have that." The owner's rules are read through this loader
#    only.
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
