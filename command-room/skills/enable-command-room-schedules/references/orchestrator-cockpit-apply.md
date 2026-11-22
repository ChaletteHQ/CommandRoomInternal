# Cockpit apply (taskId: cockpit-apply)

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this run is the one writer from the cockpit page to the book. It reads the choices the page saved and applies them through the owning writers (`cockpit_helpers:apply_commitment_choices`; apply-choices' own `cr-inbox` handlers for mail), writes each result back to the page, and nothing else. No other cockpit task reads a choice, and no other cockpit task can send mail. It posts NO widget and lands NO page of its own: the chat turn is one line, then STOP (COCKPIT1, cr1#608; four tasks per cr1#619).

Read `cockpit-shared.md` in this folder first and do its shared step 0 and shared step 1 with leg `apply`. Then this leg, then the shared last step.

**Personal sources, before the first connector read (cr1#1179).** Render `cd "$PLUGIN_ROOT" && python3 shared/scripts/workspace_access.py plan run_helper --json '{"name": "connector_reads:chat_wall", "args": {"workspace_root": "<WS>"}}'`, paste what it prints and run that, once, before any connector is read here, and follow `shared/CHAT_WALL.md`: call no tool on a server in `connectors`, show no message from a mailbox in `mailboxes` unless its sender is in that entry's `except_senders`, write nothing from either, and say nothing about what was left out.

## The leg: what the page saved, into the book

**Claim before acting (cr1#675).** Two Apply runs can overlap: the hourly fire and a press on the page, a Start over, a retried run. A press must never be applied twice, because a send would go out twice. So this run claims each choice before it acts on any, with its own id (`<this session id>`, the same `run_id` its light carries):

1. Read the waiting choices: `query` on `choices` with `where: [["applied", "==", false]]`. Keep each document's `version`.
2. Plan the claim:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"choices": [<the documents as read, each with its version>], "now_iso": "<now ISO>", "run_id": "<this session id>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:plan_claim"}'
```

3. Send its `writes` as ONE ArtifactData `batch`, exactly as they came back. A batch refused because a document changed (another run claimed it, or the owner pressed Undo) sends you back to step 1, once. Never write a claim by hand, never claim without its pin, and never act on a choice this run did not claim. Nothing to claim and nothing held (`writes` and `held` both empty): skip to **The page's own sends and drafts** below (cr1#817: a press the page made itself is already `applied` and never in this query), and when that records nothing either, the light is `done` with "Nothing waiting." and the shared last step.
4. **More than one batch (cr1#689).** One batch carries at most fifty claims; the answer's `n_more` counts what it could not. While `n_more` is more than zero, do steps 1 to 3 again for the rest, at most four rounds in one run (a round is one read, one plan, one batch). What still waits after the fourth round is the answer's `line` ("N more wait for the next run."): append it to this light's line at the end, after the counts, so the owner knows the rest is coming.

Then read the choices again (step 1's `query`, for the versions the claim left), `get` the used-token record `config/sent_tokens` (keep its version; a document that does not exist reads as no tokens), and hand them to the planner with the newest inbox fire stamp (the `fire_stamp` on `feed/inbox`, read with `get`) and this run's id:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"choices": [<the documents as read>], "inbox_fire_stamp": "<feed/inbox fire_stamp, or null>", "now_iso": "<now ISO>", "run_id": "<this session id>", "used_tokens": [<the tokens list of config/sent_tokens, or []>], "workspace_root": "<WS>"}, "name": "cockpit_helpers:plan_apply"}'
```

Eight lists come back, plus `skipped`: the choices another run holds or no run claimed. Write nothing on a skipped choice.

Every result below is written on its choice as `result`, with `applied: true` and `applied_at`, as an `update` pinned by the version rule in `cockpit-shared.md`.

**`refused`**: the result is `{"outcome": "refused", "line": <its line>}`.

**`commitment`**: one writer call. `resolved_by` may be left empty: the writer acts as the workspace's owner (cr1#678: no step gave this run the owner's id, so an empty actor wrote nothing and the light still said done).

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"choices": [<the commitment list, as it came back>], "now_iso": "<now ISO>", "resolved_by": "", "run_id": "<this session id>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:apply_commitment_choices"}'
```

A row may carry `pile` (cr1#1540: a pile cleared in one press on the Work tab), and a pile's Undo row carries `back_of` and `back_seq` (the press it takes back and the row that press wrote). Pass each exactly as it came back, like every other field: the writer asks that pile's own rule again before it writes the row, and a row whose item has left the pile comes back `refused` with its line and nothing written; an Undo whose item was changed again since comes back `already`, with nothing written. A result may carry `seq`, the row its write made: write the result whole, `seq` included, because the card's Undo names that row. The same holds on the review list, where the Looks done pile's rows arrive.

Its `results` carry one `{id, outcome, line}` per choice. A result with `outcome` `waiting` (cr1#1377: another Apply run holds that press) is the one exception to the write-back above: write nothing on that choice, so it stays waiting and that run or the next one applies it. An answer with `ok: false` wrote nothing for the rows it lists: each of its choices gets `{"outcome": "error", "line": <the answer's line>}`.

**`inbox`**: first load the mail tools, the same step the inbox task's Phase 2 runs (`inbox_helpers:connector_discovery_step`, then `inbox_helpers:mail_seams`); this run starts with its connectors deferred, exactly like the inbox task (cr1#678). Call `mail_seams` with `"pressed_send": true` in its args: this run is scheduled, and a scheduled run resolves no send tool unless it says its sends are the owner's own confirmed presses (cr1#707); without it every Send fails as if mail were not connected. No send tool resolved: every `send` choice gets `{"outcome": "error", "line": "Mail is not connected on this computer, so nothing was sent."}` and nothing is sent.

Each entry carries the `draft` the owner pressed (`to`, `cc`, `subject`, `body`) and the `thread_id` it belongs to (cr1#677). The email that goes out is that draft, in that thread: pass the entry's `draft` as the tuple's `input`, and when the handler's own row `n` names a different thread from the entry's `thread_id`, refuse the choice with the planner's stale line and send nothing. The row number is only a label; it can point at another thread once a new inbox read lands. An entry with no `draft` or no `thread_id` is refused the same way: a draft the run cannot place in its thread is not sent on a guess.

Before every `send` and `draft`, ask the reply-all gate with the mail service `mail_seams` answered (`declared_email.provider`, cr1#1251): a reply to more than one person on a mail service whose reply tool answers the sender alone (Microsoft 365), or on one the run cannot name, is never sent or drafted, because it would leave people off:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"action": "<send or draft>", "draft": <the entry's draft>, "platform": "<mail_seams declared_email.provider, or null>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:reply_reaches_everyone"}'
```

`refuse: true` writes `{"outcome": "refused", "line": <the answer's line>, "reason": "group_reply_backend"}` as the choice's result and sends or drafts nothing; never fall back to a reply to the sender alone or to a new mail outside the thread.

Before a `send`, read its thread once through the mail connector (the entry's `thread_id`) and hand the thread's messages to the gate, which decides in code:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"message_ts": "<the entry's message_ts>", "messages": [<each message of the thread as read, {"ts": ...}>], "workspace_root": "<WS>"}, "name": "cockpit_helpers:newer_message_arrived"}'
```

`newer: true` refuses the choice with the answer's `line` as its result and sends nothing. Otherwise, for a `send`, record its token BEFORE anything goes out (cr1#684): one helper call composes the record, and its answer is `set` on `config/sent_tokens` pinned by the version rule in `cockpit-shared.md` (no pin when the document did not exist):

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"doc": <config/sent_tokens as read, or null>, "token": "<the entry's token>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:sent_tokens_update"}'
```

`set` the answer's `tokens` (as `{"tokens": [...]}`) and keep the version that write returns for the next send in this run. An answer with `already: true` means the record read already holds this token (another run recorded it and sent, or is sending): write nothing and refuse the choice with the planner's reused-token line. A pinned write refused because the record changed is redone once after a fresh `get`, through the same helper call, with the same `already` rule; a write that still fails means this send is not made: its result is `{"outcome": "error", "line": "The send could not be recorded, so it was not sent; press Send again on the page."}`. A run that dies between the record and the send leaves a token no later run accepts, which is the point: a press is sent at most once, and the owner presses Send again when it was not sent. Then dispatch the tuple through `skills/apply-choices/SKILL.md`'s `cr-inbox` handlers exactly as the widget's Apply does, `send` sending through the declared mail backend and `draft` landing in Drafts; write each handler's one-line ack as the choice's `result` (`applied` on success, `error` with the ack otherwise). A choice the page did not confirm never sends; the planner has already refused it.

**`hub`** (cr1#662): the Today page's reminders and priority to-dos, and a "Missed something?" press saved before the header entry became "Something wrong?" (cr1#1017: the writer searches the book for the item, answers what it found and why it was missed, files it and records the miss). "Something wrong?" (cr1#1288) saves no choice: it opens a new chat with the problem typed in, and that chat does the looking.

**First, a live search for each "Missed something?" press (cr1#1202).** When the hub list holds a choice with `kind` `miss`, search the business mailbox and the meetings connector for it before the writer call, through the wall read at the top of this run (`connector_reads:chat_wall`). No miss in the list: skip this and pass neither `live` nor `inbox`.

1. Plan the searches:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"choices": [<the hub list, as it came back>], "workspace_root": "<WS>"}, "name": "cockpit_helpers:miss_search_plan"}'
```

2. For each entry in its `searches`, search with its `query`, at most `limit` results per source, from the last `days`:
   - **Mail:** the business mailbox only, with the mail connector's thread search (load the mail tools as the `inbox` step does, without `pressed_send`). Never a server in the wall's `connectors`; on a server that also carries a mailbox in the wall's `mailboxes`, search the business mailbox only. Each thread found is one hit `{"source": "mail", "server_id": ..., "mailbox": <the address it is in>, "thread_id": ..., "subject": ..., "sender": ..., "ts": ..., "url": ...}`: the subject and sender only, never the mail's text.
   - **Meetings:** the meetings connector's search, unless that connector is in the wall's `connectors`. Each meeting found is one hit `{"source": "meeting", "server_id": ..., "meeting_id": ..., "title": ..., "ts": ..., "url": ..., "snippet": <at most one sentence>}`.
   - A source not connected, one the wall names, or a search that fails is not listed in that press's `searched`.
3. `get` `feed/inbox` (its `filtered` list says why the inbox read set a thread aside).

Write nothing from a walled source and say nothing about it here. The writer holds every hit to the wall again, from the book, and its answer says in general words that personal sources are never searched.

Then one writer call, `resolved_by` again left empty, with `live` (by each search's `id`: `{"searched": [<"mail" and/or "meetings">], "hits": [<the hits as read>]}`) and `inbox` (the `feed/inbox` document as read, or null):

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"choices": [<the hub list, as it came back>], "inbox": <feed/inbox as read, or null>, "live": {<search id>: {"hits": [<hits>], "searched": [<sources searched>]}}, "now_iso": "<now ISO>", "resolved_by": "", "workspace_root": "<WS>"}, "name": "cockpit_helpers:apply_hub_choices"}'
```

Write each result as that choice's `result` (the whole result object, so the page can match a new item to its row; an `error` field in it stays there for the receipt). A press already in the book answers from its first write (`replayed: true`), so a retried run never writes a reminder twice. Then refresh the Today lists so the page shows them at once: `run_helper` with `cockpit_helpers:feed_mine` (`{"now_iso": "<now ISO>", "workspace_root": "<WS>"}`) and `set` the answer on `feed/mine`. A reminder the owner added is still the owner's own: the page carries their words and their day, and this run only writes them.

When a press from the Hub's add and edit landed (cr1#1431: a result with outcome `applied` whose choice's kind is `stream_edit`, `priority_edit` or `person_edit`), refresh the tab it changed too, so the edit shows now and not at the next refresh. For a `stream_edit` or `priority_edit`:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_projects"}'
```

and `set` the answer on `feed/projects`, then the Week board, which shows each work stream's name and a lane per priority:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_board"}'
```

and `set` the answer on `feed/board`; for a `person_edit`:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_people"}'
```

and `set` the answer on `feed/people`. Each read runs once however many presses called for it, with the same `file_path` rule for a large answer as the refresh step. A read that fails leaves the old feed in place and changes no result: the next refresh brings the tab up to date.

**`review`** (cr1#779): the answers under To confirm on Commitments (the Review tab until cr1#1286). One writer call, `resolved_by` again left empty:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"choices": [<the review list, as it came back>], "now_iso": "<now ISO>", "resolved_by": "", "workspace_root": "<WS>"}, "name": "cockpit_helpers:apply_review_choices"}'
```

Write each result as that choice's `result`. A result with outcome `refused` wrote nothing: a close the fence held, or a row whose answer needs a chat or a typed value. Its `line` says so, and it counts as refused. An answer with `ok: false` wrote nothing: each of its choices gets `{"outcome": "error", "line": <the answer's line>}`. Then refresh the tab: `run_helper` with `cockpit_helpers:feed_review` (`{"now_iso": "<now ISO>", "workspace_root": "<WS>"}`) and `set` the answer on `feed/review`. When one of the answers has `review_kind` `learned_rule`, also refresh the Done tab, where the new rule's Undo sits: run the step headed **Then the Record tab** in `orchestrator-cockpit-refresh.md`, both calls with their arguments as written there, and `set` the answer on `feed/week`.

**`week`** (cr1#780): the Record tab's presses (the Done tab until cr1#1537). One writer call, `resolved_by` again left empty:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"choices": [<the week list, as it came back>], "now_iso": "<now ISO>", "resolved_by": "", "workspace_root": "<WS>"}, "name": "cockpit_helpers:apply_week_choices"}'
```

Write each result as that choice's `result`. An answer with `ok: false` wrote nothing: each of its choices gets `{"outcome": "error", "line": <the answer's line>}`. The next refresh brings the Record tab up to date.

**`settings`** (cr1#781): the edits made under Settings (behind the Hub's gear since cr1#1537), a learned rule reworded, switched off or deleted, or the question budget changed. One writer call, `resolved_by` again left empty:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"choices": [<the settings list, as it came back>], "now_iso": "<now ISO>", "resolved_by": "", "workspace_root": "<WS>"}, "name": "cockpit_helpers:apply_settings_choices"}'
```

Write each result as that choice's `result`. A result with outcome `refused` or `already` wrote nothing, and its `line` says why. An answer with `ok: false` wrote nothing: each of its choices gets `{"outcome": "error", "line": <the answer's line>}`. Then refresh Settings so the page shows the change at once: `run_helper` with `cockpit_helpers:feed_settings` (`{"now_iso": "<now ISO>", "workspace_root": "<WS>"}`) and `set` the answer on `feed/settings`; a reworded or switched-off rule also moves the Record tab, so run the step headed **Then the Record tab** in `orchestrator-cockpit-refresh.md` and `set` its answer on `feed/week`.

**`mail`** (cr1#1020): the Inbox tab's presses on the owner's mail rules, a thread dropped, a rule taught from one email's note, a rule added by hand or removed, a dropped thread brought back. Nothing here sends or reads mail. One writer call, `resolved_by` again left empty:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"choices": [<the mail list, as it came back>], "now_iso": "<now ISO>", "resolved_by": "", "workspace_root": "<WS>"}, "name": "cockpit_helpers:apply_mail_choices"}'
```

Write each result as that choice's `result`. A result with outcome `refused` or `already` wrote nothing, and its `line` says why. An answer with `ok: false` wrote nothing: each of its choices gets `{"outcome": "error", "line": <the answer's line>}`. Then refresh the rules list so the page shows the change at once: `run_helper` with `cockpit_helpers:feed_mail_rules` (`{"now_iso": "<now ISO>", "workspace_root": "<WS>"}`) and `set` the answer on `feed/mail_rules`. The rules apply from the next inbox read.

**`people`** (cr1#1020): the People tab's stars, a person starred to Home or unstarred. Nothing here sends anything or changes a person's record. One writer call, `resolved_by` again left empty:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"choices": [<the people list, as it came back>], "now_iso": "<now ISO>", "resolved_by": "", "workspace_root": "<WS>"}, "name": "cockpit_helpers:apply_people_choices"}'
```

Write each result as that choice's `result`. A result with outcome `refused` or `already` wrote nothing, and its `line` says why. An answer with `ok: false` wrote nothing: each of its choices gets `{"outcome": "error", "line": <the answer's line>}`. Then refresh the people so Home and the tab show the change at once: `run_helper` with `cockpit_helpers:feed_people` (`{"now_iso": "<now ISO>", "workspace_root": "<WS>"}`) and `set` the answer on `feed/people` (the same `file_path` rule for a large answer).

**The page's own sends and drafts (cr1#817).** A Send or Draft the page made itself (cr1#710, cr1#727) went out through the mail connector with no record in the book. This run writes that record, once however many runs overlap. `query` on `choices` with `where: [["by_page", "==", true]]`, keep each document's `version`, and plan:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"choices": [<the documents as read, each with its version>], "now_iso": "<now ISO>", "run_id": "<this session id>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:plan_page_records"}'
```

Send its `writes` as ONE ArtifactData `batch`, exactly as they came back: the claim, pinned to the version read, is what makes the record land once. A batch refused because a document changed sends you back to the `query`, once; a row in `taken` belongs to another run and is left alone. Then, when `entries` is not empty, one writer call:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"entries": [<the entries, as they came back>], "now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:record_page_sends"}'
```

For each result with outcome `applied`, `already` or `refused`, `update` its row with `{"book_recorded": true, "book_recorded_at": "<now ISO>", "book_record": {"drafted_seq": <its drafted_seq>, "sent_seq": <its sent_seq>}, "book_record_line": "<its line>"}`, pinned to the version the claim write returned: a refusal (no recipient on the press) has nothing to record and is final. A result with outcome `error` leaves its row as it is, with the result's `line` written on the row as `book_record_line`: the claim lapses after twenty minutes and the next run tries again. Nothing here sends or drafts anything: the mail went out when the owner pressed; this writes the record of it.

**A meeting booked, moved or cancelled on the page (cr1#1539).** The same plan answers `calendar`: the changes the owner made to a meeting on the Hub, which the page already made on the calendar on the owner's press. They were claimed by the same batch. When `calendar` is not empty, one writer call:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"entries": [<the calendar entries, as they came back>], "now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:record_calendar_changes"}'
```

For each result with outcome `applied`, `already` or `refused`, `update` its row with `{"book_recorded": true, "book_recorded_at": "<now ISO>", "book_record": {"event_seq": <the result's own seq>}, "book_record_line": "<its line>"}`, pinned to the version the claim write returned; a result with outcome `error` leaves its row as it is with the result's `line` as `book_record_line`, and the next run tries again. This step never calls a calendar connector and never sends or drafts anything: it writes the record of a change that is already made. These count with the page's sends in the light's "recorded K from the page".

**The light says what happened (cr1#678).** Count every result written: applied, refused, and failed (outcome `error`). When nothing failed and every writer answered `ok: true`, the light is `done` with "Applied N, refused M." When anything failed, the light is `error` with "Applied N, refused M, failed K: <the first failed line>." Never `done` over a failure. When the claim's last round left `n_more` above zero, its `line` follows the counts ("Applied N, refused M. K more wait for the next run."). When this run recorded a page send or draft (cr1#817), the count follows too: "Applied N, refused M, recorded K from the page." A record that failed counts among the failed, so the light is never `done` over a press the book still lacks.
