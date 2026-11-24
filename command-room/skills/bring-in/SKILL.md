---
name: bring-in
surfaces: both
description: "Fires on: 'bring this in', 'bring these in', 'bring something in', 'bring in from a folder', 'bring in from an export'. One door to bring things into Command Room from a folder on this computer, an export, past chats or a connector. Asks once per batch whether to link each item (leave it in place, the default) or copy it in, shows one preview of the whole batch with a per-item change, and writes nothing until you accept; Undo takes the whole batch back. Keeps people you hold off limits or personal out. Does NOT fire on 'bring in context from [source]' / 'ingest my chatgpt export' (ingest-context), 'ingest this folder' (workspace-ingest), 'scan my desktop' / 'sort these into projects' (file-documents), 'backfill my history' (session-backfill), 'scan for commitments' (scan-for-commitments), or 'bring back [project]' (workspace-manager)."
---

# bring-in

One door for bringing things in (cr1#1586). The code is `shared/scripts/bring_in.py`; this skill is the conversation around it. Today's phrases for the older doors keep their skills: `ingest this folder`, `scan my desktop`, `ingest my chatgpt export`, `backfill my history` and `scan for commitments` route as before.

## Writer Contract

Before writing, read `shared/WORKSPACE_API.md`. This skill writes only through the door's one write, `bring_in:accept` on the WRITE door. The door records the batch, adds its people and companies through their writers, and copies an item into the project the owner chose. Taking an item back is the owner's Undo. The skill itself never writes a file, `entities.json` or `events.jsonl`.

## The rules this door keeps

- **The off-limits wall comes first.** The preview asks the wall before the source is so much as listed, and screens every name the batch would write. If the wall cannot be read, say the preview's `line` and stop: nothing is read or written.
- **Read an item's contents only after the first preview kept it.** Never open, quote or summarize an item the preview left out, and never say who or what it was about. Say the preview's `line`, which counts what was left out and names no one.
- **Nothing is written before the owner accepts the whole batch.** The preview writes nothing. The one write is `bring_in:accept`, called once, after the owner says go on the preview they saw, with that preview's `digest`.
- **Link is the default.** Ask once per batch: "Link them, leaving each where it is, or copy them in?" with link preselected; one word switches the batch to copy. The preview shows every item, and the owner can change any one item.
- **A link keeps a summary, never the item's own text.** Write one or two plain sentences on what the item is, after the wall, in your own words. Never paste the item's text into the summary.
- **People and companies only through the door.** Name the people and companies each item shows in the `people` and `orgs` arguments, per item; the door screens them and writes them through the people and company writers on accept. Never write `entities.json` or `events.jsonl` yourself, and never hand-write a record.
- **Nothing at the source moves.** A copy is a copy. An item no project takes stays where it is and shows in the Hub's Documents with no project pile.

## Steps

1. **The source.** Ask which source, unless the owner already said:

   ```bash
   cd "$PLUGIN_ROOT" && python3 shared/scripts/workspace_access.py plan run_helper --json '{"name": "bring_in:sources", "args": {}}'
   ```

   Paste what it prints and run that. Offer the four sources in its order, in its words. A source whose `built` is false: say "Bringing things in from <words> is not here yet. A folder on this computer or an export works today." and stop. For a folder or an export, ask for its place on this computer.

2. **Copy or link, once.** Ask "Link them, leaving each where it is, or copy them in?" Link is the answer unless the owner says copy.

3. **The first preview.** Run the preview with the source and the mode, and nothing else:

   ```bash
   cd "$PLUGIN_ROOT" && python3 shared/scripts/workspace_access.py plan run_helper --json '{"name": "bring_in:plan", "args": {"workspace_root": "<WS>", "kind": "<folder | export>", "source": "<the place the owner gave>", "mode": "<link | copy>"}}'
   ```

   Paste what it prints and run that. `result.stop` true, or the door refused the call: say `result.line` (or, with no line, "I could not check this against the people you keep off limits or personal, so I stopped before writing anything.") and stop. Otherwise `result.items` are the items the wall kept.

4. **Read and place.** For each kept item, read it from its source, then:
   - pick the one project it belongs to from the book's threads (`places`: item_id to thread id); leave it out of `places` when no project fits;
   - for a link, write its summary (`summaries`: item_id to one or two sentences);
   - note the people and companies it shows, per item (`people`: item_id to a list of `{"canonical_name", "email"}`; `orgs`: item_id to a list of `{"canonical_name", "domains"}`). An item whose people include somebody the owner keeps off limits or personal is left out whole, with everything it names.

   The arguments can be too long for one command line. Write them as one JSON object in this chat's own scratch folder, never a file in the workspace, and name it by `args_file`:

   ```bash
   cd "$PLUGIN_ROOT" && python3 shared/scripts/workspace_access.py plan run_helper --json '{"args_file": "<your scratch>/bring_in_args.json", "name": "bring_in:plan"}'
   ```

5. **The whole-batch preview.** Run `bring_in:plan` again with every argument from steps 2 to 4. Show `result.lines` as the preview, then `result.line` when it is not empty, then: "Say go to bring these in, or change any item." An owner's change (one item to copy or to link, another project, no project) goes into `overrides` or `places`; run the preview again and show it again. Keep the last `result.digest`.

6. **Accept.** Only on the owner's go, add `digest` (the last preview's) and `accepted_by` (the owner's person id) to the same scratch file, change nothing else, and call the write door once:

   ```bash
   cd "$PLUGIN_ROOT" && python3 shared/scripts/workspace_access.py plan run_writer --json '{"args_file": "<your scratch>/bring_in_args.json", "name": "bring_in:accept"}'
   ```

   Paste what it prints and run that. Say `result.line`. `status` `changed`: the source or the arguments moved since the preview, nothing was written; run the preview again. Keep `result.batch_ref`.

7. **Undo.** "undo" after a bring-in takes the whole batch back through the bare-undo route with `result.batch_ref`: each link leaves its project, each copy moves to the archive, each item left at its source leaves the pile, and each contact and company the batch added is archived. The source is never touched.

## The activity log is append-only (MANDATORY — CONTRACT Rule 31)

This skill adds to the activity log only through the door's one write, `bring_in:accept`. **The activity log is never rewritten by hand.** No step here, and no turn this skill runs in, may edit, truncate, reorder, delete lines from, back up and rewrite, or restore that log — and may never instruct anyone else to.

- A duplicate or malformed line is **quarantined through the cleanup skill's
  existing path**, never deleted (`recover_corruption.py` for malformed lines,
  `seq_health.py --mark` for a duplicate entry number).
- Correcting writes this skill made means **appending a reversal through
  `brain_undo.undo_batch`** with the batch ref the run advertised (step 7's
  Undo) — a receipt and a real `undo`. `undo` after a re-run means exactly that
  batch, or the words "nothing to reverse"; never an improvised drop, an
  invented supersede, or a hand-edited file.
- If you believe the log itself must change, **STOP and say so in plain
  words.** Do not do it, and do not offer to.

## Not here yet

- Past chats and connectors (cr1#1586, later pull requests). Until then, `backfill my history` (session-backfill) reads past chats.
- The Hub's own entry for this door.

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
