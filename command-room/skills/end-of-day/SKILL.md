---
name: end-of-day
surfaces: both
description: "Close the day in one pass. Fires on 'end of day', 'close out my day', 'daily wrap', 'wrap my day', and the scheduled evening chat: reconciles the day's sent mail and chat, synthesizes how the day went in grounded prose, reads the day in the plate's shape (opened, closed, slipped), and asks at most two pre-picked confirms drawn from the Staff Meeting's own weekly five — a stated tomorrow renders as fact. Does NOT fire on 'morning briefing', 'weekly recap', 'friday wrap', 'end session', or 'process the call'."
---

# End of Day — the evening bookend

**For:** the 5 PM weekday close, and any time the CEO says the day is over. One
chat, not two. The day-close should make the open book SMALLER most days, and
what it reads it writes to memory with a resolvable pointer — the daily close
IS the daily memory commit.

The scheduled fire runs
`skills/enable-command-room-schedules/references/orchestrator-past-meetings.md`
under EITHER of two taskIds — `end-of-day` (the current id, minted by EOD2) or
`past-meetings` (the predecessor, still registered on every machine that has
not taken the rename offer). Both map to that one file, so both fire this same
pack at the same hour, forever; the rename is proposed, never applied, and a
workspace that ignores it loses nothing. The file is the fire's exact steps;
this skill is the on-demand entry point and the source of truth for the render
contract all paths share. The fire's receipt keeps the `past-meetings` task_id
on both ids (`end_of_day.TASK_ID`) so the day-close series never splits.

## Access — where the files are (CONTRACT Rule 22)

Every step below that reads or writes the workspace goes through the access layer, by the rules in this block (the one `shared/WORKSPACE_ACCESS.md` defines). On a legacy or local seat the four shell lines at its foot are what run; on a merged seat every workspace step is a `workspace_access.py` verb line, run where the data is.

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

## Phase 0 — Catch the upkeep up first (HEAL1 — on demand only, and silently)

**Skip this whole phase on a scheduled fire.** A seat whose scheduler works already runs the background upkeep on its own cadence; a seat whose scheduler cannot reach the workspace never gets here at all. This phase exists for the one moment that is reliable on every seat: the customer opened their workspace and typed.

**Never judge for yourself whether the upkeep is owed: ask, and do what comes back.** One verb, one call, on every seat, rendered by `workspace_access.py plan run_helper` exactly as the Access preamble describes and never hand-written (MIGRATE3-EOD: the in-process block this replaced opened the workspace in this session's own shell, which a merged seat does not have):

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=end-of-day python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"surface": "end-of-day", "workspace_root": "<WS>"}, "name": "maintenance_dispatcher:catch_up_plan"}'
```

Its `result` is the plan, the same answer `surface_drivers.maintenance_catch_up(<WS>, "end-of-day")` gives, read where the data is; call it `plan`.

**Run each job by its OWN leg, not by its name (FIX3 F3-6, ruling R-RW-5).** Every row in `plan["jobs"]` carries `leg` — the whole command, already ending `--fired-via manual --triggered-by <this surface>` — and `env`, the same two answers as variables. On a legacy or local seat, paste `job["leg"]` verbatim; where the job is a SKILL rather than a script `leg` is empty, and then export `job["env"]` before running it. On a merged seat the plan line is what crosses the door and the layer forwards the same two variables. This is not decoration: on 2026-09-21 four upkeep jobs ran inside a hand-typed morning brief and all four recorded themselves as a scheduled fire, because "execute each job's skill end to end" gives a flag nowhere to go.

1. **`plan["catch_up"]` is false → do nothing, go to Close.** It is false whenever the last upkeep slot was served, whenever another surface caught up minutes ago, and whenever nothing is due. Write no receipt for a false plan: an empty one would make a stopped scheduler look alive.
2. **True → run `plan["jobs"]` BEFORE the Close phase**, in the order they come back, one at a time, never in parallel. Execute each job's skill end to end, then score it with `maintenance_dispatcher.job_counts_as_complete(job_id, receipt_validated=…, run_reported_nothing_due=…)`. Never widen that predicate by hand. The weekday family only — the Sunday family is held back for `weekly recap` and `run maintenance`, and the plan has already done that filtering. This runs BEFORE Close for the same reason Close runs before Read: a close that reconciles an unreconciled substrate scores the day against the wrong book.
3. **Then ONE receipt, and only because jobs ran:** `maintenance_dispatcher.maintenance_receipt(WORKSPACE_ROOT, jobs_due=…, jobs_completed=…, jobs_failed=…, fired_via="manual", triggered_by="end-of-day")`. On a merged seat that is one `plan append_jsonl` line. One per catch-up, and it lands before anything this surface writes for itself.
4. **`plan["refused_container"]` is not empty → those jobs are not yours to run from here.** They write, and a write only happens where the files are. They stay owed and the next run from the right side of the bridge picks them up. `plan["refused_line"]` is the sentence for `run maintenance` and the health check — never for this surface.
5. **SAY NOTHING ABOUT ANY OF IT.** Not a count, not "caught up first", not a mention. What the jobs DID to the customer's own rows reaches them the way it always has — credited by door, each batch with its one undo. The condition of the plumbing belongs to the health check and the weekly maintenance report and nowhere else.

## The four phases, in order. The order IS the contract.

1. **Close** — reconcile what the day discharged, IN THIS FIRE. Same machinery
   the maintenance jobs run (`reconcile_sent_commitments.reconcile_and_receipt`
   for mail, `chat_reconcile.reconcile_chat_and_receipt` for chat), same
   cursor, same audit event. The 17:45 maintenance pass then finds an advanced
   cursor and closes nothing twice.
2. **Reconcile** — resolve what changed: people, deal stages, identities. The
   existing maintenance machinery, run same-day and receipted. No new logic.
3. **Read** — the seven blocks below. This is the surface.
4. **Capture, last and fenced** — meeting processing is the FINAL leg, and it
   is last for a reason that is not tidiness: a capture written earlier in the
   fire would be scored by this same fire's reconcile and counted by this same
   fire's read. Capture last is the circularity fence.

## ⛔ If the pack does not arrive (SPEC SURFACEFIX1 5.3 / amendment E-5)

When the driver cannot build the day-close it prints **one sentence** —
*"End of Day could not render tonight — say `end of day` to retry."* — in
place of the `CR-EOD-PACK:` line, and writes the receipt that records the
failure. **Post that sentence and stop.** No traceback, no exception text, no
file path, no module or function name, no "here is what I found instead".

**Never build the pack by hand.** Not from the ledger, not from the
helpers, not "just the closes". On 2026-09-13
this fire crashed on a serialisation bug; the chat rebuilt the day-close by
hand, re-ran the capture leg while it was at it, and duplicated seven
decisions, three meetings and three receipts that no sanctioned collapser can
undo. A hand-built day-close is not a degraded day-close — it is a different
product, with no receipt, no fences and no undo. **Do not re-run capture to
"recover" either:** a meeting processed once writes no new rows (M's ruling
R4, 2026-09-13), and the close reads what capture already wrote.

## What renders, and what is only computed (SPEC EODSYNTH1)

`surface_drivers.build_end_of_day_pack(workspace_root, mode=...)` builds
everything in ONE call (the t3 FB-9 pattern). **Two lists govern it and the
difference between them is M's 2026-08-23 ruling written down:**
`end_of_day.RENDER_ORDER` is what reaches the screen, in order, and
`end_of_day.COMPUTED_ONLY` is what still runs, still lands on the `pack_run`
receipt, and renders nowhere. **Un-render, don't unbuild.**

**THE SCREEN IS COMPOSED IN CODE (CUT-PLATE, 2026-09-06 — M's hold: no
decisions block and no question in the day-close; the evening reads the day
in the PLATE's shape).** `pack["screen"]` is `end_of_day.compose_screen(pack)`
— every line the fire may post, in `end_of_day.SCREEN_ORDER`: the plate's
eod cut FIRST (*"Your plate today — N opened · N closed · N slipped"*, the
rows in their blocks, one pointer), then `day_went`, `what_it_meant`,
`worth_remembering`, `slipped_prose`, `echoes`, the coach's delta, a STATED
tomorrow as fact, and the sign-off. **The coverage strip prints nowhere on
this surface any more either (M's ruling R3, 2026-09-13 — SPEC SURFACEFIX1
5.1)**, and neither do the substrate alarms or the dark-surface lines
(HEALTH1, 2026-09-07 — M's ruling supersedes CUT-PLATE's "health lines
LAST"):** `compose_screen` never calls `add()` for `coverage`, `alarm_lines` or
`dark_surface_lines`, and the driver stops calling `task_alarm.
dark_surface_lines` at all (its render-once ledger would otherwise be
consumed here before the weekly `cleanup` report — the surface that now owns
both — ever saw it). Print `pack["screen"]["text"]` VERBATIM; it is the whole prose turn. The
composer RAISES (`ScreenShapeError`) if a retired sentence — "survived N
closes", "consecutive close", "did not move", "what is tomorrow about" — or an
asking line reaches the composed text, so the v5.28.0 shape cannot come back
quietly. `RENDER_ORDER` / `COMPUTED_ONLY` are untouched (they are the
receipt's vocabulary); the screen is where the placement lives now.

| Block | What it is | The rule that governs it |
|---|---|---|
| `alarm_lines` | `substrate_health.substrate_alarm_lines` | **RETIRED FROM THIS SURFACE (HEALTH1, 2026-09-07).** Still computed on the pack (harmless, side-effect-free — other readers use it), never placed on `pack["screen"]`. Reported by the weekly `cleanup` maintenance run instead |
| `dark_surface_lines` | TASKALARM1 — the dead-surface alarm, task_alarm.dark_surface_lines | **RETIRED FROM THIS SURFACE (HEALTH1, 2026-09-07).** The driver hard-codes `[]` and never calls `task_alarm.dark_surface_lines` here at all — not just unrendered, uncalled, so its render-once ledger stays available for `cleanup`'s weekly pass, the surface that reports it now. See below |
| `coverage` | What this fire actually READ, per capability | **RETIRED FROM THIS SURFACE (M's ruling R3, 2026-09-13 — SPEC SURFACEFIX1 5.1).** Same class as the health lines above: a statement about plumbing, home = the `system health` check and the weekly `cleanup` maintenance report. Still computed on every fire and still receipted (a `COMPUTED_ONLY` member now), placed on `pack["screen"]` never. The ONE survivor is `end_of_day.coverage_number_caveat` — one clause, composed in code, appended INSIDE the plate block next to the figure it qualifies. See below |
| `day_went` | One grounded paragraph: what moved today | Composed in code by `eod_synthesis.compute_day_went` from the LEDGER's own fields and today's named closes. Printed verbatim; never extended |
| `what_it_meant` | THE ARC READ (SPEC EODARC1): which arcs moved today, which consequence-carrying arcs did not move, where the day's weight went | Composed in code by `eod_synthesis.compute_what_it_meant` over DECLARED arcs only. A sentence with no arc attached does not belong in the section (`eod_synthesis.drop_rows_only`); a genuinely empty day says so honestly in one grounded line; never an arc the model inferred |
| `worth_remembering` | Decisions and notes logged today, 1–4 lines | Each line IS a row, not a summary of one |
| `slipped_prose` | The slips whose consequence is STATED | Prose, not a list. No denominator, no "137 slipped". Everything else that slipped is silent here and appears in the morning |
| `echoes` | At most two labelled precedent echoes | Absent by default; each cites a precedent BY ID or is refused |
| `tomorrow` | The wide calendar look, the rollover, the day-intent draft | **A STATED intent renders as fact** (`end_of_day.TOMORROW_STATED_LINE`); the draft PROPOSAL is computed and receipted and **never rendered, never asked** (CUT-PLATE — M's hold 2026-09-06: no question in the day-close). `tomorrow is about [X]` is how the CEO states it |
| `sign_off` | Computed, never composed | Zero urgent → "Nothing else needs you before tomorrow's brief." verbatim |
| `plate` | The day's delta in the plate's shape (PLATE1 night 2, D7 `eod`) | **Renders FIRST on the screen (CUT-PLATE)** — see below |

**Computed, receipted, rendered NOWHERE:** `score` (with its `ledger` and
`first_move`), `wins`, `slipped`, `confirm`, the tomorrow PROPOSAL, the
coach's Layer 1 and push, and the arc read's "did not move" sentences.

- **`plate` (SPEC PLATE1 night 2 — D7 `eod`; CUT-PLATE 2026-09-06).** The
  day's delta in the plate's own shape: `pack["plate"]["text"]` is
  `plate_view.render_plate(build_plate(ws, since_iso=<the ledger's anchor>),
  "eod")` — *"Your plate today — N opened · N closed · N slipped"*, then the
  opened rows in their blocks, the closed titles (done / dropped), the rows
  whose date passed in the window and are still open, and ONE pointer to
  `what's on my plate`. Same model, same words, same rows as the plate and
  the morning brief; the window is the ledger's (`since_ts`, day-floored the
  same way), so the delta and the ledger measure one span. **It is the FIRST
  block of the screen** — PLATE1-N2 shipped it computed-only under
  EODSYNTH1's no-lists rule, the v5.28.0 attended test (B2.2) showed the
  fire still rendering the old prose instead, and M ruled for the plate
  shape: the day-close now reads the day as the plate does, and the rows
  are read-only (no verbs, no numbers to tap). A close a later `undo`
  reversed is not counted closed (POLICY1-B (c)). `pack["plate"]["refused"]`
  with the one plain line is the no-owner case (D8) — never lanes.
- **`confirm` ranks on the plate's evidence (PLATE1 P7; EODRANK1 DD-1
  superseded).** Inside the pending-first stake, the newest proposal riding
  the row (`data.proposal` — score, evidence, evidence_ts — read off the
  projected row when the resolution policy wrote it, else folded from the
  stream) ranks a row with a completion signal first, then by score, then
  by capture recency; each shown row carries its `proposal` so the reader
  can see what ranked it. Still computed only.

  **A proposal is evidence on the row, never a question (M's ruling
  2026-09-03).** A guess a later transcript shows was done closes as done
  automatically — with the quote, receipted, undoable — and never arrives
  as an ask; only the narrow middle band writes a chip at all, and that
  chip acts or retracts itself at the policy's four-day window. No sentence
  on this surface, or on the morning brief, may say a proposal is pending,
  awaiting your answer, or waiting on you.

- **`score`** — `n_planned` / `n_closed` / the book-at-open arithmetic are all
  still computed and still on the receipt, because weekly-recap and the trend
  surfaces read them. No sentence on this surface carries one. The score
  anchors on the morning plan, so a day that drifted from its 7 AM plan scored
  as a failure regardless of what actually got done; on 2026-08-19 the grade
  sat next to three closed wins and read as a contradiction.
  `eod_synthesis.assert_no_score` is a code fence over the composed text and it
  RAISES.
- **`wins`** — the named closes feed `day_went`. No block of their own.
- **`slipped` / `confirm`** — they feed `slipped_prose` and the MORNING
  surfaces. The confirm/drop queues and the needs-your-call rows render on the
  morning brief's needs-attention lane and on the `needs-your-call` / `my-plate`
  chats, where the operator is in triage mode; 5 PM is wind-down.

**No widget, and at most two pre-picked confirms (CUT-PLATE — M's hold
2026-09-06 — WIDENED BY CITATION: R-N10-3, M's design rule 2026-09-06 — "I don't mind a couple of those questions appearing on end of day"; built FOLD1-A fix round 1).** The
day-close posts `pack["screen"]["text"]` and nothing else: no tomorrow card,
no Confirm / Edit, no Slipped section, no Needs-your-call section, no person
candidates, no score. `["widget"]` is None by construction, and
`pack["screen"]["asks"]` is 0 or the count of the DECLARED `eod_questions`
block — never more than `end_of_day.MAX_SCREEN_QUESTIONS`, never a new
question class, and never the decisions block (`confirm` stays in
`end_of_day.COMPUTED_ONLY`). Those rows are drawn from the Staff Meeting's
existing weekly five through `quiet`'s one shared budget, so the evening
spends from that allowance rather than adding to it; a seat with nothing to
ask renders no block at all, which is the ordinary evening. The tomorrow draft the pack still computes is data for the
receipt and the morning; the CEO states tomorrow with `tomorrow is about
[X]` (workspace-manager, BK1), and that stated intent renders here as fact.

**`confirm_ids` is EMPTY** (`end_of_day.NUMBERED_BLOCKS` is `()`): the evening
renders no numbered ROW-LIST, so it numbers none, and a `[n]` tap against that
map is refused in plain English. The tomorrow confirm resolves through
`end_of_day.resolve_intent_confirm` off the same receipt and never used that
map. Numbering a row that does not render is the defect, not a spare
capability — it makes every tap past it resolve against something nobody saw.

### Answering the evening's two questions (FOLD1A fix round 2, REVIEW_FOLD1A R-2)

The `eod_questions` block is the one thing on this screen the CEO answers, and
it has its OWN numbering, its own map and its own writer — `confirm_ids` and
`NUMBERED_BLOCKS` are untouched, exactly as the tomorrow proposal has its own
key and its own resolver.

- **The card.** When the block rendered, the pack carries
  `pack["eod_questions"]["transport"]` — a validated two-row page built by
  `eod_question_budget.render_question_widget` through
  `widget_transport.render_and_persist`. Relay `transport["html"]` VERBATIM as
  `show_widget`'s `widget_code`, beside the composed text. Never re-render it,
  never restyle it, never build a card of your own: the day-close still has no
  card of its own and this is the one cited exception (R-N10-3). No transport
  on the pack means no card — post the text and stop. **Load the widget tool before anything else** (`shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first): on the merged and half-merged seats it is deferred, and a listed deferred tool counts as present; ToolSearch loads it, its setup tool runs first, then the relay.
- **Each row carries exactly ONE tap, the pre-picked `confirm`.** That is the
  design rule (one tap each, the likely answer already picked, no three-way
  menus). The other three queue verbs still exist and still work — on
  `needs your call`, on demand.
- **The typed answers are NUMBERED**: `yes 1`, `no 2`, `skip 1`. Resolve each
  through the surface's ONE resolver, exactly as every other gesture here does,
  read where the data is (`end_of_day.resolve_choice`, one verb per number):

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=end-of-day python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"action": "<yes | no | skip>", "n": <the number typed>, "workspace_root": "<WS>"}, "name": "eod_helpers:resolve_choice"}'
```

  Its `result` is the target. `ok` false: say its `refusal` verbatim and write
  NOTHING.

- **Never a bare `yes`.** The block does not print one and neither do you: a
  bare affirmative belongs to whatever turn the customer is in, and a product
  that claims it hijacks every "yes" anyone ever types. The number is the
  answer, the same rule `resolve_choice` already keeps for every other verb.
- **The write is ONE call, and it is the queue's own writers underneath**,
  through the WRITE door (`eod_helpers:answer_eod_questions` runs
  `eod_question_budget.apply_eod_answers` beside the data). `answers` is the
  rows `resolve_choice` returned ok for, each paired with the answer the
  customer gave it; `individually_named` is ONLY the rows the customer
  actually tapped or numbered (a gesture that names nothing passes an empty
  list and a weak row is HELD and reported, exactly as it is on the queue;
  never widen this on your own); `source_ref` is the first resolved row's own:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=end-of-day python3 shared/scripts/workspace_access.py plan run_writer --json '{"args": {"answered_by": "<user person_id>", "answers": [<one {"id": <the resolved id>, "answer": "<yes | no | skip>"} per resolved row>], "individually_named": [<every resolved id>], "source_ref": "<the first resolved row source_ref>", "workspace_root": "<WS>"}, "name": "eod_helpers:answer_eod_questions"}'
```

  Inside it: `yes` → `needs_review_queue.confirm_items`, `no` →
  `drop_items`, `skip` → nothing written. ONE batch id over the whole answer,
  ONE `eod_question_answered` receipt, and a bare `undo` reverses all of it
  through the reversers those two writers already have. NEVER call
  `confirm_items` / `drop_items` directly from here and never append an event
  beside them — the batch is what makes the undo one gesture.
- **The ack is the answer's `ack`** (`answer_ack` on the writer's own result,
  composed beside the data), one plain sentence: counts and words, never an
  id, never a batch id, never an event type. Print it verbatim.

### The self-scored question, on a seat that opened the coaching door (SPEC SURFACES2_11c Lane 3 item 3)

A seat whose coaching shape is `named` or `coached` may also be asked ONE
question about the behaviour it named for itself — *"How did {behaviour} go
today, 1–10?"* — inside the same block, inside the same two, and never on top
of them.

- **The confirms take the slots first** (ruling R-9). A confirm is an act
  waiting on the reader; this is a note to themselves. Two confirms due means
  no coaching question at all. The arithmetic is
  `eod_question_budget.coach_question_slots`, and the driver has already run
  it — the block on the pack is the answer, never something to recompute here.
- **An `observed` seat is never asked.** The coaching shape is the switch:
  `turn off coaching` ends this with nothing else to unset.
- **It is answered by number, with a number**: `score 1 7` is "row one, seven
  out of ten". One resolver, the same positional resolution as every other
  gesture on this surface, through the WRITE door (`eod_helpers:score_coach_answer`
  runs `end_of_day.resolve_coach_score` beside the data; it records ONE
  `coaching_answer` row):

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=end-of-day python3 shared/scripts/workspace_access.py plan run_writer --json '{"args": {"answered_by": "<user person_id>", "n": <the row number>, "score": <the number typed>, "workspace_root": "<WS>"}, "name": "eod_helpers:score_coach_answer"}'
```

  A `refusal` in its `result` is said verbatim and NOTHING was written: a
  score on a confirm row, or a number outside one to ten, are both refused by
  name.

- **The ack is the answer's `ack`** (`coach_answer_ack` on the writer's own
  result): print it verbatim. It says the number landed and nothing
  else. No comparison with yesterday, no encouragement, no reading of what the
  number means — the reading is the seat's, which is the whole point of asking.
- **There is no undo, and the ack says so** (ruling R-5, default taken).
  Nothing changed, so there is nothing to put back; never offer `undo` for this
  answer and never imply one.
- **Answered once a day.** A second fire on the same evening does not ask
  again and spends nothing more out of the week — `coach_answered_on` reads
  the answer already on the book.
- **A row answered earlier the same day is not asked again tonight.** Nothing
  bookkeeps that: answering a queue row through its one writer is what makes it
  stop being an open row anywhere, so it is simply not a candidate. Do not add a
  second "already asked" list.

### Grounding is the build, not a footnote

Every synthesized sentence carries the rows it came from, and
`eod_synthesis.drop_unreferenced` removes any sentence whose ref set is empty
BEFORE it can be composed. The chat shows prose; the persisted brief and the
receipt show the join. Six rules, each a pin:

1. An unreferenced sentence never renders.
2. A quote is admitted only from a LABELLED transcript and never assembled
   across speakers (`eod_synthesis.admit_quote`) — the meeting connector
   fabricates attribution on label-free transcripts.
3. Tier 2 joins only to arcs the substrate DECLARES (`end_of_day.declared_arcs`
   reads objectives, the day's stated intent, org relationships with a live
   thread, active workstreams, deals with a stage; SPEC EODARC1 adds the
   consequence-carrying open commitments minted in the composer). Never an
   inferred theme.
4. Tier 3 refuses an echo with no precedent id, bans the manufactured-profundity
   phrasings outright, and rations to two.
5. The numbers in the prose are read off the ledger dict the receipt carries
   (`eod_synthesis.ledger_numbers`), so the prose and the receipt are the same
   fields by construction — "closed 4" here and "0 closed" there cannot recur.
6. A row held out of sight by the held tier or the personal firewall never
   reaches a sentence (`eod_synthesis.visible_rows`).

### The arc read (SPEC EODARC1) — a recap lists what changed; a synthesis says what it means for what you are running

That sentence is the build, and it is named here so it cannot drift back:
the first live EODSYNTH1 render was true, grounded, and entirely about ROWS —
coverage lines, what didn't move, what was late — and not one sentence was
about the day. The `what_it_meant` block is now the ARC READ, answered in
order:

1. **Which arcs moved today**, grounded in what happened — today's closes,
   meetings and decisions joined (strictly, by id and thread linkage, never
   by similarity) to the arcs the substrate declares: objectives, the day's
   stated intent, **org relationships with a live thread** (partnerships,
   clients — every non-self org in the entity register with an active
   thread), the unclaimed active workstreams (the product tracks), deals
   with a recorded stage, and consequence-carrying open commitments no other
   arc tracks (`eod_synthesis.mint_commitment_arcs`).
2. **Which consequence-carrying arcs did NOT move** — what is waiting, and
   on whom, off the OPEN book with the same strict join. The consequence is
   the row's own stated field (a due date, a named counterparty, a stated
   blocker, a meeting it gates — `eod_synthesis.arc_stated_consequence`),
   never an inferred one.
3. **Where the day's weight went** — one sentence relating effort to arcs,
   counted off today's own rows and never against a plan.

**The fence:** `eod_synthesis.drop_rows_only` runs over every sentence the
composer produces — a sentence whose refs name no arc this render was handed
cannot reach the screen, so the section structurally refuses to enumerate
rows as synthesis. The one exemption is the honest empty-day line (kind
`empty_day`), which still carries the day window as its ref: a genuinely
empty day says so, it is never padded and never silent.

**Ruling 3 (M, 2026-08-25): deals are read as context, never as state.** The
pipeline tracker is not load-bearing anywhere in the arc read: an org with
recent activity and a live thread IS an arc whether or not a deal row tracks
it, a deal row with a stage can only ADD an arc, and nothing in the read
raises when deal state is absent, stale, or malformed. No writes to deal
state, ever. The section stays prose-only — zero new actions, buttons,
proposals, or writes; the day-close asks nothing of its own (CUT-PLATE; the
only questions are the <= 2 pre-picked confirms, R-N10-3, M's design rule 2026-09-06 — "I don't mind a couple of those questions appearing on end of day"; built FOLD1-A fix round 1) — a stated
tomorrow renders as fact, never as a question.

### The coach (SPEC EODCOACH2) — patterns across evenings, and the intent-vs-outcome delta

M's ruling, same date, after seeing the full-build sample: two more prose
layers survive on top of the arc read above — Layer 1 (pattern memory across
closes) and Layer 2 (the intent-vs-outcome delta, plus one push line). Layer
3 (a curated action strip) is PARKED: it collided with M's own one-
interaction ruling, and this build does not render it. `eod_coach.py` is the
module; `pack["coach"]["text"]`, printed verbatim, right after `echoes` and
above `tomorrow` — never inside `render_order`, for the same reason
`catchup` is not: it is a key the fire renders by instruction, not a member
of a tuple pinned elsewhere by exact equality.

**Prose only, ZERO new interactions** — the same fence EODARC1 keeps: a coach
sentence carries `{text, refs, tier, kind}` and nothing else, no verb, no
checkbox, no proposal. The day-close asks nothing of its own (CUT-PLATE; the only questions are the <= 2 pre-picked confirms, R-N10-3, M's design rule 2026-09-06 — "I don't mind a couple of those questions appearing on end of day"; built FOLD1-A fix round 1); a stated tomorrow renders as fact.

**Layer 1 — pattern memory — COMPUTED, NOT ON THE SCREEN (CUT-PLATE).**
Reads the last 7 EOD packs off disk (the same
`_hq/.system/briefs/end-of-day-pack-*.json` audit copies this driver already
writes) and counts, never infers: an arc unmoved 3+ CONSECUTIVE closes while
carrying a consequence; a commitment recurring in the meeting-gated slip on
3+ of the days examined with no send between; and the survival count on the
single oldest consequence-carrying overdue item. At most 2 are kept, dropped
by strength — `eod_coach.compute_patterns` — and they ride the pack as
`coach["patterns"]` and the deduped `coach["layer1"]`. **What PRINTS is
layered by the seat's coaching shape** (CORRECTED 2026-09-15, REVIEW_NIGHT11C
H-7 — this paragraph used to say flatly "they no longer print", which stopped
being true when night 11c's coaching train landed).
`eod_coach.SCREEN_LAYERS_BY_SHAPE` is the map: an **observed** seat gets the
delta alone — the CUT-PLATE screen the v5.28.0 attended test earned, with
"has now survived 7 closes" off it and M's ruling for less on the card
intact; a **named** seat gets the delta plus ONE counted pattern line
(`eod_coach.CAP_SCREEN_PATTERNS` = 1); a **coached** seat gets those plus at
most one sourced reading, capped at `eod_coach.KNOWLEDGE_PER_WEEK` (2) per
ISO week. `eod_coach.SCREEN_LAYERS` is now the observed tuple only. The full
Layer 1 block and the push line still ride the pack un-printed on every
shape. **Honest absence:** fewer than 3 prior packs on disk and the layer
computes NOTHING.

**Layer 2 — the delta and the push.** `eod_coach.compute_intent_delta` reads
the day's own STATED `day_intent` (EODFIX1's id linkage) against today's
closures and open book, grounded strictly through the item's
`commitment_id` — never inferred from the item's own words. At most one
sentence: "You said tomorrow was about X. It didn't move." or the honest
positive, "...it shipped." **Honest absence:** no STATED record for the day
being closed and this renders nothing.

`eod_coach.compute_push` is the one push line, computed ONLY when Layer 1
kept a pattern — the strongest one, restated as its own count plus a
concrete, countable cost. Never an imperative, never a to-do. **Computed,
persisted (`coach["push"]`, `coach["layer1"]`), not on the screen (CUT-PLATE).**

**The repetition fence (anti-nag).** The same push forced a THIRD
consecutive close renders once more, NAMING the repetition, then goes quiet
for a 3-close cooldown. The state lives on THE PACK RECORD
(`coach["push_state"]`) — this driver's next fire reads it off the one prior
pack on disk, exactly as it always writes one; there is no second store.

### Section names are join keys

The slipped ROW-LIST became the slipped-with-consequences PROSE, so the FRP1
config key moved with it: `slipped_section` → `slipped_prose_section`, carried
across by `end_of_day.migrate_section_config`, never by name match. A rename
that does not migrate does not fail — it DEFAULTS, and the workspace silently
loses a decision its owner made. `end_of_day.slipped_prose_enabled` reads the
new key and falls back to the old one, which is the belt to that migration's
braces. `tone: "scoreboard"` named a rendering that no longer exists and
migrates to `journal`, with the old value preserved under
`tone_before_eodsynth1`.

A ninth key, `catchup`, is not a block: it is the fire-level LABEL a late
day-close arrives under, and it renders above everything (see "The catch-up
read" below). On every non-degrade fire its `renders` is False and there is
nothing to place.

`coach` (SPEC EODCOACH2) is the same shape of exception, at the other end of
the surface: it renders by instruction, right after `echoes` and above
`tomorrow`, and it is not in `RENDER_ORDER` either — see "The coach" above.

### `coverage` — retired from this surface (M's ruling R3, 2026-09-13)

The fire still asks for email, calendar and chat per capability and still
receipts every gap under `connector_gaps`. What changed is where a reader
meets that record.

EODLEDGER1's original cost measurement was real: a chat cursor sat five days
behind while the surface reported the day's closes with no qualification at
all, and a calendar outage rendered byte-identically to a genuinely empty
tomorrow. The reader could not tell *nothing happened* from *I could not
look*. COVERQUIET1 then narrowed the strip to days that had something to
disclose. Neither fixed the thing M objected to on 2026-09-13, reading his
own day-close: *"a connector this fire needed was not read; chat 2 days
behind"* is a sentence about PLUMBING, and he had already ruled that class
off the morning brief on 2026-09-07.

**The ruling (R3):** connector-reachability and coverage lines are off the
brief and off End of Day, exactly as the health lines are. **Home = the
`system health` check and the weekly `cleanup` maintenance report**, both of
which read the same fields and are asked for by someone who wants them.

**What that means mechanically.** `coverage` has left `RENDER_ORDER` and
`BLOCK_ORDER` and joined `COMPUTED_ONLY`, alongside `score`, `wins`,
`slipped` and `confirm`. It is computed on every fire, in full, and it lands
on every receipt under `blocks_computed_only` — un-render, don't unbuild, the
same move EODSYNTH1 made for the score. `compose_screen` never calls `add()`
for it. `coverage_has_disclosure` and `coverage_render_lines` are unchanged
and still exported: they are the health check's and the maintenance report's
question now, not a render decision this surface makes.

**You place no part of it.** No disclosure lead, no per-capability line, no
reduction clause, no "read through Friday — 5 days behind", no `connector_
gaps` sentence. **Do not call `coverage_render_lines` here, and do not
compose a sentence of your own about what was or was not read.** If you are
printing `pack["screen"]["text"]` as the contract says, this happens for you.

**THE ONE EXCEPTION — a gap that changes a rendered number.** COVERQUIET1's
own intake design (`DESIGN_2026-08-26_coverage-lines-silent-unless-they-
change-the-numbers`, folded into R3) carves out exactly one case: when a leg
this fire could not read is a leg one of the RENDERED NUMBERS is counted
from, that number carries ONE caveat, ADJACENT to it — *"Mail was not read
today, so these counts may be short."*

`end_of_day.coverage_number_caveat(pack)` composes it and `compose_screen`
appends it inside the plate block, so nothing can re-order the caveat away
from the figure it qualifies. **One clause, never two** — a second caveat is
the strip coming back a sentence at a time, which is why the function returns
a single string and not a list. Only mail and chat can earn one
(`NUMBER_BEARING_CAPABILITIES`): those are the legs the day's counts are
drawn from. A calendar or meetings gap earns none — the calendar feeds a
LIST, which speaks for itself, and a caveat on a list is a reachability
sentence wearing a caveat's clothes.

**"Not read" and "nothing there" are still different claims.** Where a
section would have been built from a leg that was not read, render **no
section** rather than an empty one: silence about a leg is honest, "nothing
on your calendar" when the calendar was never reached is not.
`coverage["capabilities"]["calendar"]["read"]` and `tomorrow["calendar_
available"]` are ONE boolean by construction; never write a sentence that
puts them in conflict, and on this surface never write a sentence about
either.


### `dark_surface_lines` (TASKALARM1) — retired from this surface (HEALTH1, 2026-09-07)

**This used to render by instruction, directly under `alarm_lines`. It no longer does, and the driver no longer even CALLS `task_alarm.dark_surface_lines` from this fire** (`with phases.phase(eod.PHASE_ALARMS)` in `surface_drivers.build_end_of_day_pack` hard-codes `dark_surface_lines = []`).

Why the call itself moved, not only the print: `task_alarm.dark_surface_lines` is source (SPEC TASKALARM1) — the watchdog's own `late` / `receipt_gap` / `never_authorized` classes, capped at 3 worst-first with an "and N more" tail, render-once per (task, dark-window) through the module's own ledger. **The ledger is shared across every surface that calls it** — that sharing is exactly the mechanism M's ruling now uses: the weekly `cleanup` maintenance run is the only remaining caller, so it is the only surface that marks (and reports) a dark spell. If this fire kept calling the helper just to discard its return value, it would mark the same finding "seen" hours before `cleanup`'s Sunday pass ever ran, and the customer would never see it anywhere — the exact silent-loss failure mode M's ruling exists to prevent. This is also why `dark_surface_lines` no longer feeds `end_of_day.coverage_has_disclosure`'s dark-surface branch in practice: the pack always hands it an empty list, so that branch never fires from this driver (SPEC COVERQUIET1's OTHER four disclosure triggers are unaffected and untouched).

Empty list, always, on this surface — never a padded all-clear, because there is nothing to pad: the finding, when real, now surfaces once a week in the Monday note.

**Fix round 1 (review finding F-3, MED) — a circularity worth naming plainly.** The Monday `cleanup` run is now the ONLY scheduled caller of `task_alarm.dark_surface_lines`, and the maintenance task itself is one of the surfaces that helper can flag as dark. If the maintenance run is the thing that stopped firing, nothing on a schedule says so any more — the on-demand `health check` is the only remaining route, and it has to be asked for. This is a consequence of M's own ruling (all four health kinds off every customer surface), not a bug in this fire's code, and it is open as a ruling for M (R-2 in `REVIEW_HEALTH1_2026-09-07.md`) rather than decided here.

### `score.ledger` — what the day did to the open book

Present on BOTH score branches, `no_plan` included: a day with no morning plan
still moved the book. **COMPUTED, RENDERED NOWHERE since CUT-PLATE (2026-09-06)** — the
day-close renders `pack["screen"]["text"]` in `SCREEN_ORDER`, which has no ledger, and
`assert_no_score` RAISES on a rendered score line. Read the shape below to understand the
field; never print it, and never quote its numbers in the screen.

`status: "movement"` — book at open → `+opened` → `−closed` → `−dropped` → book
now, with the delta named. When `residual_line` is set, print it: the four
movements did not account for the whole change and saying so is cheaper than a
number that does not add up. Never absorb the residual into a movement.

`status: "no_opening_figure"` — **no morning fire, so there is "no opening
figure on record" and NO arithmetic.** The opening figure is READ off the
morning fire's own `brief_state`, never inferred. Render the line as given and
stop: no substituted count, no delta of zero, no "flat day". A guessed baseline
is indistinguishable from a measured one once it is on screen —
`NO_PLAN_LINE`'s doctrine, one field down.

**Information count (CLUSTCOUNT1, 2026-08-26)** — `pack["brief_state"]["headline"]` may carry `information_count` + `information_line` (e.g. `"9 items, 41 rows"`) beside `book_now`'s bare total — the same one-line-per-real-world-item read the queues already give, run here by `compute_brief_state` over tonight's confirmed open set. ADDITIVE and PRESENT ONLY when something actually clustered. **When `information_line` is present, print it beside `ledger["line"]`'s book-now figure** ("book now: 41 — 9 items, 41 rows"); when absent, the ledger line stands alone exactly as before — never compute a substitute, never invent "N items, N rows" restating the total when the key is not there.

### The catch-up read — a late day-close arrives labelled, not skipped

On the degrade tier (>24h late) this surface RENDERS, opening with
`pack["catchup"]["lines"]`: the span it covers, how late it is, and the slot it
missed. It used to post `late_fire`'s `degrade_notice` alone, so a skipped
Tuesday was never scored — not that evening, not ever. **Do not post
`degrade_notice` here**: it says "Skipped the full End of Day", which is true on
every other scheduled surface and false on this one the moment it renders, and
it is a shared constant that is deliberately not being reworded. One read covers
the whole span; never one surface per missed day. And `skip_render` is
untouched — a slot already delivered posts its `ack` and stops. Late is not
duplicate.

### `score.first_move` — render the STATUS, not just the line

`first_move` is `{text, status, checked_against, matched}`, and the status is
the whole point of the field. `open` — this morning's suggested first move is
still live; render it as this morning's plan. `stale` — something that closed
today NAMES that line (`matched` says which). **NOTHING IN `first_move` IS RENDERED on the
day-close since CUT-PLATE (2026-09-06)** — the paragraph below describes the field for the
surfaces that may carry it, not this fire. Where a surface does carry it, `stale` renders as CONTEXT about
this morning's plan and **never as an instruction**: *"This morning's first
move was X; it closed at 12:30"* is right, *"Your first move is X"* is the
defect this replaces. `unverifiable` — nothing was on file to check it
against, so say that plainly in the same breath or leave the line out; never
promote it to a live instruction. `first_move: null` means the brief printed
none: render nothing, and never compose one.

This rule is here and not only in the fire's own text because the manual
family renders the same pack. The failure it closes was field-observed: two
live packs on 2026-08-17 carried the same already-discharged line, byte for
byte, at 9 PM, as though it were the next thing to do.

### `wins.title_source` and `wins.more_line` — computed, rendered nowhere

**SPEC EODSYNTH1 un-rendered this block.** Its named closes are an INPUT to
`day_went`; there is no wins section on the surface any more, so nothing below
about `title_source` or `more_line` describes something a reader sees. Both
rules are kept — not as render instructions, but because they still govern what
`compute_wins` may CLAIM about a row, and a row whose name was guessed would
carry that guess straight into a paragraph. `window_source` at the end of this
section is the one that matters most, and it is the reason it is kept in full.

Each row says where its name came from. `snapshot` / `joined` — render the
title. `generic` — the row's `title` IS the whole honest sentence (*"a
commitment was closed"*), so render it as is and never dress it up with a
name, an id, or a guess at which one it was.

`more_line`, when non-empty, prints VERBATIM as the block's last line. The
block shows at most six rows and a live day can put five times that through
it, so the pointer is what keeps the cap a render bound instead of a silence —
the same rule the morning brief's needs-attention lane keeps. Never top the
block up to close the gap, and never report `n_total` as if it were the number
of rows on screen.

**`slipped` and `confirm` carry the same line, from the same helper
(`end_of_day.more_line`), and every one of them STATES ITS DENOMINATOR** —
"shows 3 of 41", not "shows at most 3". A cap without a denominator is not a
summary; it is a claim about size, and it was a wrong one: `slipped` bound 3 of
41 and `confirm` 5 of 67 in silence. One helper and one shape, so no block
acquires its own dialect of "there is more than this".

**An overdue item asks once, then it rests (SPEC OVERDUE1, M's ruling R-3:
"I would do it for 3-4 days") — AND SINCE SPEC EODSYNTH1 THE ASK HAPPENS IN
THE MORNING.** The rule below is unchanged down to the comparison; what moved
is the surface that performs it. `end_of_day.apply_overdue_ask` is the shared
verdict both bookends call, `end_of_day.mark_lane_asked` is the morning's
write, and this fire builds its slipped block with `ask=False` — it rests what
is resting and asks nothing, so its `asked_ids` is empty and
`end_of_day.mark_slipped_asked` has nothing to do on this surface. Read every
paragraph below as a description of the RULE, and the morning brief's
needs-attention lane as where it is now performed.

 Three items due Aug 6-8 rendered identically
in this block every night for two weeks. Past a few nights, repetition stops
being a reminder. So an item **3 or more days past its due date** (the knob is
`overdue_ask_after_days` on this skill's config; default 3) is pinned to the
TOP of the block once, with a direct question — *"{title} — 8 days overdue.
Done, new date, or drop?"* — carrying the block's existing verbs. From the
next fire it is **suppressed from this block only** until it is answered, and
one trailing line says how many are resting and where to find them.

Resting is not disappearing: the item stays on the open book, stays on
`my plate`, stays in the morning brief's needs-attention lane, and stays
inside this block's own `n_total`. **Answered** means any real movement —
`mark done`, `drop`, `push to [date]`, or a re-word / re-owner / re-date. A
new due date re-arms the clock from that date, with no second write: the mark
records WHICH deadline it asked about, so it stops matching the moment the
deadline moves. The mark itself is `commitment_state.mark_asked`, modelled on
`watch_gate.park_in_watch`, and it is deliberately **not movement** — asking
about a quiet item must not make it read as freshly touched.

Two consequences worth stating plainly, because both are places the rule
could have gone quiet and does not. **The sign-off still counts a resting
item** — it stopped being repeated, not late — so "3 items are still overdue"
stays true on a night the block shows one of them. And **being asked is not
being touched**: the mark is exempt from the un-confirm bar, so if you confirm
an old overdue item and the evening chat asks about it that night, `undo
confirm` still works the next morning. A watch park is a decision and still
blocks that undo; a question the system asked is not.

This is NOT `cap_needs_attention`'s rotation rule and must never be described
as one. That rule exists so nothing is suppressed FOREVER; this one exists
because something is shown EVERY NIGHT. Opposite problems, separate code.

`window_source` says which window these rows were read from. `morning_anchor`
— the day's morning brief fired and the window opens there. `day_floor` — no
morning brief fired, so the window opens at workspace-LOCAL midnight of this
fire's own day and never earlier. **A day with no morning brief still renders
its wins**; it counts them from midnight, and `more_line` is already spelled
for that window (*"…more moved since midnight"* rather than *"…more moved
today"*). Print it as given and never re-word it to claim "today" over a
floored window. This rule is here and not only in the fire's own text because
the manual family renders the same pack.

**AND THE PARAGRAPH INHERITS THAT FLOOR, because it inherits these rows.**
`day_went` is composed over the same closure window (`pack["window"]["wins"]`
and `pack["window"]["closures"]` name it), so a day with no morning brief still
gets a paragraph and the paragraph is about the day FROM MIDNIGHT — never a
whole workspace's history read as today. Unfloored, that read returned 2,334
rows on 2026-08-19 on a day that had moved none of them. The paragraph never
claims a window in words; the window is on the receipt, where a reader can
check it. Never describe the floored day as having no wins, and never describe
it as a full day's history.

**Monday** adds the prior week's day-scores after the day-close blocks, and the
weekly development read SLOT, which renders NOTHING until DEVREAD1 ships.
**Friday** is a plain day-close with no hand-off line: the Friday chat must not
compete with `weekly-recap`.

The roll-up has **three row shapes** and each renders what its own row says:

| Shape | Renders |
|---|---|
| The chat never ran that day (`recorded: False`) | "no close was recorded" |
| The chat ran but predates the score (`counted: False`) | "the day ran before this count existed" |
| Both (`counted: True`) | the numbers |

**The middle shape is the one that matters at ship.** This fire serves a taskId
that has existed for many releases, so on the first Monday after the upgrade
every day of the prior week is a pre-EOD1 receipt: a real fire, with no score on
it. It renders its own line. Never a zero, never a dash, never a blank row, and
never a week summarized as slow — none of those is a thing the record says.
Zero is a measurement; these two are not.

### Why the wording is load-bearing

"Not recorded" and "not done" are different claims and only one of them is
supportable. A missing close event says nothing wrote the close down; it does
not say the work did not happen. Say the second and the CEO argues with the
surface, and a surface the CEO argues with stops getting read. The phrasing
lives in `end_of_day.NOT_RECORDED` / `NOT_RECORDED_LINE`, so there is one place
to read it and one place to change it.

## The close reconciles; it does not fetch (SPEC EODSPEED1)

EODPHASE1's live records measured the 9–27-minute evenings: the pack build
costs 7–10 seconds; the rest was connector fetching and redundant re-scans at
close time. The remedy moves the day's fetching earlier — it never trims a
check.

- **The incremental capture pass.** The capture leg also runs as the
  `meeting-capture` job inside the already-authorized `maintenance` task
  (6:45 / 12:45 / 5:45 slots — zero new scheduled tasks), executing the
  orchestrator's Phase D verbatim: same canonical writers, same admission
  gates, no relaxed floors. It is SILENT — writes, briefs, and its own
  receipt (`eod_incremental.log_capture_pass_receipt`, a `pack_run` under
  task id `meeting-capture`, never under `past-meetings`) and nothing else.
  The close remains the one narrator.
- **The close re-verifies over disk.** Its window computation is untouched;
  meetings the pass captured come back already-processed from dedup, so the
  5 PM fire fetches only what arrived since the last pass. Their briefs
  still render — the render set's `briefed_prior` status counts them as
  briefed and the coverage sentence names them ("captured earlier by the
  background pass"). A day where no pass ran degrades to fetch-at-close
  exactly: slower, complete, never a thinner close.
- **Stale-evidence skips are recorded once** (the 237-row class): the CRU
  walk ledger (`eod_incremental.already_walked` / `record_walk`) lets a fire
  honor a prior complete walk of the same evidence inside the evidence
  window instead of re-fetching and re-deriving the same refusals. The
  matcher and its floors are untouched; an empty ledger walks normally.
- **The budget:** a close on a day whose captures are current lands within
  5 minutes of the slot at full depth (`end_of_day.CLOSE_BUDGET_MS`); the
  receipt's `close_budget` verdict plus the EODPHASE1 phase records are the
  instrument. The verdict set is pinned byte-identical across incremental
  and bulk arrival (`tests/run_eodspeed1_test.py`).

## Capture: the admission gates, and the flip that is OFF

The capture leg's doctrine is CAPTUREFLOW's, unchanged: every extracted
commitment goes through `meeting_capture.route_meeting_captures`, and a
below-floor capture routes to the REVIEW tier. It is never silently dropped and
never deleted — a wrong floor call costs one tap in the queue rather than a
lost promise.

**Decision 1's routing flip ships DARK, and this is the paragraph that says so
out loud.** `shared/scripts/held_tier.py` carries a config-gated branch that
would route the weakest of those rows to a HELD tier — out of sight: not
queued, not badged, not counted, retrievable on request. The knob is
`weak_capture_routing` on this skill's config and its default is `review`.

**Turning it on is not this workspace's decision, and the fence is
`shared/scripts/operator_capability.py`.** `held_tier.enable_held_routing`
calls `held_tier.capability_status(...)`, which asks
`operator_capability.capability_status("held_tier_routing")`, and REFUSES while
that capability has not been granted. Nothing is written on a refusal, not even
a pending marker.

The grant lives in the plugin payload
(`shared/config/operator_capabilities.json`), so it arrives with a release and
by no other route. This is deliberate: the flip turns on when the people who
build Command Room are satisfied the disposition is safe. They reach that on
their own book — nobody reads this workspace's captures to decide it. It is
**not** a measurement a workspace takes of itself. It used to be, and that
was wrong in a way worth stating once — the thing that produced
the measurement never shipped, so the check was red in every workspace forever
and read as an accusation about that owner's accuracy when it meant that the
instrument had never been delivered.

Three things follow and none of them is optional:

- **Never enable it from inside a fire.** Enabling is a deliberate action taken
  once the disposition has been judged safe. A fire that flips it has decided
  the thing that judgement exists to decide.
- **Never route around the refusal** by writing the config value directly. The
  value alone does not enable anything: `held_tier.flip_status` re-asks the
  capability on every fire, so a stored request is a request and never a grant.
- **Never tell an owner their own numbers are the reason.** The refusal
  sentence is pinned once, as `held_tier.REFUSAL_LINE`, and it says the flip is
  not a setting in this workspace and that nothing about their numbers is
  holding it back. Both halves are true and the second one is the point.

When the flip is off — which is every workspace today — `apply_held_routing`
hands the routing back unchanged and the held lane is empty. Off is
byte-identical to no flip at all, and that is asserted, not assumed.

## The shape of the close is the same ten settings as the brief (CUSTOM2)

The day-close does not have its own answer to "how should this be grouped".
It reads the SAME ten settings the morning brief reads, out of its own store,
which is why one sentence — "group my brief by workstream" — moves both
surfaces and the weekly wrap in a single act, and why nobody has to be told
twice.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"surface": "end-of-day", "view": <the view: number_line, rows, sections>, "workspace_root": "<WS>"}, "name": "eod_helpers:render_for_fire"}'
```

The answer is `brief_settings.render_for_fire(workspace_root, view, surface="end-of-day")` itself — call it `out`.

`render_for_fire` reads and renders in the one legal order: settings first, the
free-text notes folded in beneath them, and a note can never move something the
reader has stated. Reading the halves yourself is `settings_for_fire` then
`render_surface` — and `render_surface` REFUSES free text without the list of
stated settings, so the ordering cannot be lost by leaving an argument off.
Group the day's rows through it (or `group_rows` when you are composing the
screen yourself) rather than deciding the grouping in prose — the composer runs
`assert_number_leads` over what it built, so the plate's number still leads
whatever the settings say, and `out["said"]` carries anything a filter did that
the reader has to be told about.

Changing them is `morning-briefing`'s door ("customize my morning brief",
"reset my brief", or any single sentence naming one part). This skill reads;
it does not ask.

## Mechanics

- **The narration scan (CUT-C item 8, MANDATORY).** The pack builder
  scans every line it composes (`surface_drivers.build_end_of_day_pack`
  runs `validate_chat_output` over the ledger, the sign-off, the tomorrow
  line, the synthesis and the coach text). Whatever YOU compose on top of
  the pack — the slipped narration, a row's plain-English restatement, the
  intro — is scanned the same way before it posts: run
  `validate_chat_output(<the composed close text>)` from
  `chat_output_renderer.py`; it raises `LeakDetectedError` on a raw id
  (`person_NNN`, `project_NNN`, a wire id), an event or field name, a path
  or a score. ABORT the post and rewrite the sentence with the entity's name
  (`narration_names.humanize`). NEVER catch the error and post anyway.
- **Personification.** Read `shared/PERSONIFICATION.md` and call
  `personification.get_brain_name(workspace_root)`. The evening intro is
  `"Evening, {first_name} — {brain_name} closing out your day."` — the ONLY
  greeting permitted, and it renders only if the persona block permits:
  when the workspace CLAUDE.md persona block (`## How {brain_name} talks
  to …`) says skip pleasantries, or its Never-line forbids greeting
  openers, drop the intro line and open with the day's substance (STYLE1
  D6 — the persona outranks this shape). One name in the intro (when it
  renders), one in the sign-off; nowhere else. The sign-off signature
  stays either way — identity, not pleasantry.
- **Receipt BEFORE post (BRIEFFIX1 Item C).** `end_of_day.log_end_of_day_receipt`
  is the ONE receipt writer for this fire and it runs before the surface posts.
  It carries `confirm_ids` (the numbered map, in the order the surface numbers
  them) and the day-intent proposal, so a later tap resolves positionally. A
  fire that posts a numbered surface with no recorded numbering makes every
  one-tap action on it ambiguous, and the resolver refuses those rather than
  guessing.
- **Writer wall.** Personal-account items may render; they write nothing. No
  entity write, no commitment write, no capture from a personal-account read.
- **Every close carries its pointer.** Each close this fire writes goes through
  `commitment_state.close_commitment(..., source_ref=<the sent message's
  artifact key / the chat pointer / the meeting id>)`. A close with nothing to
  point at still lands — since SPEC PROVMINT1 the writer mints
  `session:<source_skill>:<now>` for it and marks the ref `surface_minted`, so
  it points at the ACT rather than at nothing, and PROV1's bare marker is no
  longer reachable from this writer. The floor is not a licence to drop a
  pointer this fire HAD: a minted ref counts as its own share of the coverage
  split rather than inside the artifact-backed number, and
  `end_of_day.unsourced_closes` still reports it as a close citing no artifact.
- **Widgets.** None (CUT-PLATE — M's hold 2026-09-06). The day-close
  renders no card and asks at most two pre-picked confirms;
  `pack["screen"]["text"]` is the turn.
  A row-list that ever returns to this surface goes through
  `widget_transport.render_and_persist` byte-exact — but none does today.
- **The fire introduces itself as End of Day.** The lateness banner, the
  re-run ack and the degrade notice name the SURFACE this fire serves
  (`schedule_config.serving_display_name`, read by `late_fire`), so a
  machine still registered under the predecessor id `past-meetings` never
  hears "your Past Meetings" from its own day-close (the v5.28.0 attended
  test did). The sidebar name the health check and the watchdog use is
  unchanged (SPEC EOD2).
- **Timezone.** Every rendered timestamp goes through
  `shared/scripts/tz.py::to_local(value, workspace_path=<WORKSPACE>)`. The
  fire's own date comes from `end_of_day.workspace_today`, which resolves
  workspace-local: at 9 PM Pacific the UTC calendar has already rolled over,
  and this fire runs in that window.
- **First-run (FRP1).** Three decisions, rendered once ever as the footer:
  tone (scoreboard or journal), the slipped section on or off, the sign-off on
  or off. Defaults live in `held_tier.CONFIG_DEFAULTS`.

## Connectors — ask per capability, skip and receipt what is absent

This build reaches for three capabilities and no more: **email**, **calendar**,
**chat**. Each leg asks for its OWN capability through the seam
(`tool_discovery.discover_for_category(...)` with
`connector_config.declared_backend(...)`), and a capability that is not present
is SKIPPED and recorded on the fire's receipt under `connector_gaps` — never
silently, and never as an empty section that reads as "nothing there".

The asks are written per capability from day one precisely so CONN1/CONN2 can
add Drive and DocuSign later by adding rows, not by redesigning the fire.

## One-tap verbs

Every verb below resolves through `end_of_day.resolve_choice(workspace_root,
n, action=...)` against the fire's own receipt. A stale or missing map is
refused in plain English — never clamped, never guessed.

**NOT OFFERED BY THE DAY-CLOSE SINCE CUT-PLATE (2026-09-06).** The day-close renders no
numbered ROW-LIST (`NUMBERED_BLOCKS` is `()`), so no `[n]` tap reaches any row of this table — the evening's only numbered thing is the `eod_questions` block, which carries its own map (fix round 2, R-2)
from that fire and one typed there is refused. The table is the verb contract for the
surfaces that DO number rows (the plate, the held queue); read it there.

| Verb | Block | What it does |
|---|---|---|
| `mark done [n]` | slipped, confirm | Closes through `commitment_state.close_commitment` WITH `source_ref` |
| `push to [date]` | slipped | `commitment_state.apply_later` — moves the due date on the owner's own item (`commitment_updated` carrying `new_due`), or dates a mute when the item is owed to them |
| `draft [n]` | slipped | Hands off to `email-writer` per the EW1 delegation rules |
| `drop [n]` | slipped, confirm | The existing drop path, unchanged |
| `confirm [n]` | confirm | The existing confirm path, unchanged |
| tomorrow confirm (bare = rank 1) / `1`\|`2`\|`3` | tomorrow | SPEC TOMPICK1 — `end_of_day.resolve_intent_confirm(..., pick=<rank or None>)` narrows the up-to-three ranked candidates to the ONE picked, then `day_intent.write_from_proposal(..., origin="wrap")` writes that one intent. **Not offered by the day-close since CUT-PLATE** (the proposal is never rendered, so there is nothing on screen to confirm); the resolver stays for the receipt's data and the on-demand path, and `tomorrow is about [X]` is the way to state tomorrow |
| `add person [n]` | person_candidate | `person_candidates.resolve_candidate(action="add person")` — creates the contact with every observed spelling as an alias, then drains every capture that was blocked on the name |
| `same as [existing] [n]` | person_candidate | Same call, `action="same as [existing]"` — the alias write, then the same drain |
| `not a person [n]` | person_candidate | Same call, `action="not a person"` — suppresses the PROPOSAL for that name, permanently and per org. Never a capture |

The three candidate verbs are legal on the `person_candidate` block and
nowhere else, and `mark done` / `drop` are refused ON it: there is nothing to
close on a name. Those rows are DERIVED (`person_candidates.derive_candidates`
reads the pending queue live), so `resolve_choice` hands back the row's own
`data` payload with them — a candidate has no substrate id to look up. The
fire's receipt carries `person_candidate_counts` (counts only, never a name),
which is what makes "did the waiting pile shrink?" answerable.

A tomorrow-block confirm writes `origin="wrap"` because the CEO tapped. The
pre-confirm draft is `origin="proposed"`, exists transiently, and is never
rendered as fact — `day_intent.load_day_intent` skips proposed rows by default,
so a surface cannot render a guess as the CEO's word by forgetting a flag.

## Routing (full trigger corpus)

The complete trigger family and its fences. The description carries the stems
the runtime router reads; this section is the declared family and is binding at
fire time.

**Fires on:** 'end of day', 'close out my day', 'daily wrap', 'wrap my day',
'close my day', 'end my day', 'close out the day', plus the scheduled evening
chat and its Run Now button.

(Trigger phrases in this section are single-quoted deliberately: that is the
house convention the mechanical matcher reads. Everything in the fence list
below is BACKTICKED for the opposite reason — a quoted phrase outside a
negative clause reads as a claim on it, and this skill claims none of them.)

**DOES NOT fire on:** 'add end of day' — that is a REGISTRATION ask, not a
day-close (SPEC EOD2: it is the rename switch, and the exact phrase the
retirement line hands a customer still running `past-meetings`). It routes to
`change-schedule` → registration Phase 6, which registers `end-of-day` and
disables the predecessor in one step. This one is single-quoted deliberately,
unlike the backticked list below: it sits inside the negative clause the
mechanical matcher actually scans, so quoting it here is what makes the fence
real rather than decorative. The bare `end of day` still fires this skill — a
phrase only becomes the registration ask when the `add` verb is on it.

Also DOES NOT fire on:

- `morning briefing` / `brief me` / `start my day` — that is `morning-briefing`,
  the other bookend. The two share a receipt vocabulary and nothing else.
- `weekly recap` / `what happened this week` / `friday wrap` — that is
  `weekly-recap`. The Friday End of Day is deliberately a plain day-close with
  no hand-off line so the two never compete for the same moment.
- `end session` — that is `workspace-manager` wrapping a working session, which
  is a different thing from closing a calendar day.
- `process the call` / `meeting notes` / `process the meeting` — that is
  `meeting-notes`, one meeting at a time. The End of Day fire processes the
  day's meetings as its final leg; a single named call is still that skill.
- `triage my inbox` — that is `inbox-triage`.
- `tomorrow is about [X]` / `what's tomorrow about` — that is
  `workspace-manager`'s day-intent handler (BK1). Since CUT-PLATE (2026-09-06) the End of
  Day fire OFFERS NOTHING for tomorrow — it states what is on file as a fact and asks
  nothing; the day-intent read and write are the manual path and stay there.

## See also

- `shared/scripts/end_of_day.py` — the blocks, the words, the receipt, the resolver
- `shared/scripts/eod_synthesis.py` — the prose, and every grounding fence on it
- `shared/scripts/eod_coach.py` — pattern memory and the intent-vs-outcome delta (SPEC EODCOACH2)
- `shared/scripts/held_tier.py` — the dark flip
- `shared/scripts/operator_capability.py` — the operator grant the flip is fenced on
- `shared/scripts/day_intent.py` — the tomorrow record (SPEC BK1)
- `skills/enable-command-room-schedules/references/orchestrator-past-meetings.md` — the scheduled fire
