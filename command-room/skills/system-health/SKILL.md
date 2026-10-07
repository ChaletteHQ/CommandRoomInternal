---
name: system-health
surfaces: both
description: "On-demand scheduled-task health check AND the system's self-report. Fires on: 'health check', 'is everything running', 'system health', 'are my tasks running', 'did my tasks run', 'why didn't my [task] run', 'what did you change', 'what's waiting on me', 'staff meeting', 'run our staff meeting' (the full Staff Meeting surface — pending-proposal queue + change feed, same as the weekly chat). Reads run receipts, scheduler records, the proposal queue, and the change feed; answers in plain English what's running, what changed, and what's waiting on you. Health check is read-only — names the fix, never registers or edits. Does NOT fire on 'weekly cleanup' / 'maintenance' (cleanup), 'set up command room schedules' (registration), 'change my schedule' / 'add staff meeting' (change-schedule), 'usage report' (usage-report), 'prep me for [meeting]' (call-prep), or 'process the meeting' (meeting-notes)."
---

# system-health

The on-demand face of the scheduled-task watchdog (`shared/scripts/task_watchdog.py`) — and, as of LB1, the system's full self-report: not just "is everything running" but "what did you change" and "what's waiting on me". The CEO asks and gets a straight answer grounded in artifacts — substrate receipts, scheduler records, the Living Brain's audit trail — never in what a past fire narrated (the Bug #98 lesson, generalized). The same watchdog used to ride the morning brief as a light daily pass; HEALTH1 (2026-09-07) retired that pass from the brief in full — the finding now surfaces on a schedule only via cleanup's Monday note (weekly deep pass) — and this skill exists so the customer never has to wait for that pass either.

## Skill Boundary (v2.1; scope grown LB1)

- **Owns:** the on-demand health diagnosis of Command Room's scheduled tasks + workspace binding; the on-demand change-feed and pending-queue read (Steps 4); the Staff Meeting surface (Step 5 — the scheduled `staff-meeting` chat's orchestrator wraps THIS surface).
- **Does not own:** fixing anything. Registration/repair is `set up command room schedules`; cadence changes are `change-schedule`; weekly maintenance is `cleanup`; usage/cost telemetry is `usage-report`. Resolutions on queue items belong to apply-choices → each item's own writer.

## Writer Contract

The health check (Steps 1–3) and the self-report read (Step 4) are read-only — no events, no config, no files. The Staff Meeting surface (Step 5) writes exactly three things, all through canonical helpers: the widget file persisted by `widget_transport.render_and_persist` itself, and the `staff-meeting` fire receipt via `receipts.log_receipt`. (Until WRAPSTAFF1 4.7 it also appended `relationship_move_suggested` events; the moves section left this surface, so it no longer writes them — the standalone relationship-moves skill still owns that emission and its 7-day exclusion window.) Nothing else — every mutation on a queue item happens later, through apply-choices dispatch, on the user's click.

<!-- SCHEDULER DISCOVERY >>> -->
```text
SCHEDULER DISCOVERY (SCHEDDISCOVER1) - rendered by schedule_backend.discovery_step(); never edit by hand.
A scheduler tool you cannot see yet may only be waiting to be loaded. Load first, look second, then decide.
1. LOAD FIRST, every time. Claude Code and the Claude app: run the host's tool search once per query,
   exactly as written, asking for up to 10 results each:
       +claude-code-remote create trigger list triggers
       +scheduled-tasks create scheduled task list tasks
   The older desktop app, which has no tool search: read its full connector tool listing, end to end.
2. LOOK SECOND. Hand ONLY the names that loaded, as plain strings, to this skill's availability guard
   and to schedule_backend.select_backend below. A scheduler name still waiting to be loaded is not a
   scheduler: it goes only into step 3's TOOLS, as {"name": <id>, "deferred": true}.
3. THE OPENING LINE. Render this one call with `workspace_access.py plan run_helper --json '<it>'` and
   paste what it prints (the older app, or Code on this computer: `workspace_access.py run_helper` here):
     {"name":"schedule_backend:opening_lines","args":{"tools":<TOOLS>,"env_mode":"<CR_ENV>","availability":<AVAIL>,"folder_attached":<FOLDER>,"reloaded":<RELOADED>,"error_text":<ERROR>}}
   TOOLS = the loaded names and any waiting one; AVAIL = the guard's answer, as it came back;
   FOLDER = is a workspace folder attached to this chat (true/false); RELOADED = false; ERROR = null,
   or the scheduler's own error text when one of its calls failed (then call again). Say each line of
   `result` verbatim, once, as the first thing this skill says about schedules, before any listing or
   registration; it replaces the no-scheduler sentence. An empty result adds nothing: the guard's own
   refusal stands alone, and a step that says nothing about schedules still says nothing. Add nothing
   of your own about where the chats run or why.
   Refused because this chat has no workspace folder: say "This chat has no workspace folder attached, so it cannot see your scheduled chats; open a chat with your Command Room folder attached." instead.
4. If that line said the tools are loading: refresh the host's connector tools once, run step 1
   again, then steps 2 and 3 with RELOADED = true. The answer after that reload is final.
```
<!-- <<< SCHEDULER DISCOVERY -->

## Step 1 — Gather the two evidence sources

1. The scheduler listing through `schedule_backend.plan_list` — the live scheduler records (taskId, lastRunAt, enabled, prompt).

   **Ask availability FIRST, and ask it of the code, never of a tool name (TRUTH1, SPEC_NIGHTM2_LANES §3 (a)).** Before planning any listing, run `schedule_config.scheduler_availability(<this session's tool names>)`. Two answers, two different facts, and they must never be blurred into one:
   - `available: False` with `reason: "scheduler_unavailable"` — there is no scheduler of any kind in this session. **Pass `task_records=None`** (the literal `None`, never `[]`) and pass `backend=None`. Do not attempt the listing; there is nothing to list from.
   - anything else — execute `plan_list`, `normalize` the result, and pass that list, **plus `backend=<the selected backend's id>`** (`schedule_backend.select_backend(...)` already returned it), so an empty listing on a cloud seat is read as "set up in the older desktop app" rather than as a blind vantage.

   `None` and `[]` are two different registry states and `health_verdict` renders a different sentence for each: `None` means "no scheduler tool here", `[]` means "the scheduler answered and holds nothing". Substituting one for the other tells the customer something that is not true about their own computer. Never probe for a tool by name to decide this — the availability helper is the one place that knows, and a name a session does not expose is exactly what this check is about.

   This step used to reach for the scheduler first and read its absence as silence, so a section could fail outright on a seat where the tool is simply not present. It no longer can: with `task_records=None` every check below either runs on receipts alone or is named as not-made, and none of them raises.
2. The workspace substrate — resolve `[WORKSPACE_ROOT]` via the canonical CONTRACT.md Rule 22 discovery preamble.

## Step 2 — Run the watchdog

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
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"backend": "<the selected backend's id (cloud / legacy), or null when Step 1 said no scheduler is available>", "records_known": <true, or false when Step 1 said no scheduler is available>, "task_records": [<the NORMALISED records from `schedule_backend.plan_list` (executed, then `normalize`), one task dict each>], "workspace_root": "<WS>"}, "name": "staff_meeting_helpers:health_report"}'
```

The verdict is computed where the data is, and the answer is one JSON object:
`vantage`, `summary_line`, `lines`, `info_lines`, `reports` —
`task_watchdog.health_verdict(ws, task_records=records, backend=backend)` — plus
`stale_prompts` (`schedule_refresh.prompt_body_drift(records,
plugin_version=<the installed version>)` — task ids with a BODY drift; this is
the list that earns the one line), `stale_stamps`
(`task_watchdog.check_prompt_versions(records, installed)`, the diagnostic stamp
read — informational, never earns a line) and `dark_surfaces`.

HEALTH3 (F-T2-7): hand the listing in WHOLE, every record the scheduler
returned, each with its `folders` (and `trigger_id`) when `normalize` carries
them; never drop, merge or pick records yourself. The door forwards this seat's
`CR_DEVICE_WORKSPACE`, and the helper counts THIS seat's records only: another
computer's copies of the same chats never decide a task's last-run time or any
count (the switched-off ones are named once, in `info_lines`). On a cloud seat
each counted entry of `reports` also carries `stamp_line`: the app's own
last-run time for that task and its next run (Step 3 renders them).

The two drift lists, as the helper computes them and as this check has always
spelled them (the contract, not a step to run):

```text
stamps = tw.check_prompt_versions(records, installed)
drift = sr.prompt_body_drift(records, plugin_version=installed)
    'stale_prompts': drift,                                   # task ids with a BODY drift (earns the one line)
    'stale_stamps': [f for f in stamps if f.get('stale')],   # informational — never earns a line
```

`records_known: false` is how "the literal None" crosses the door: when Step 1
said no scheduler is available, pass `"records_known": false` (and
`"backend": null`); the helper then hands `health_verdict` `None`, never `[]`.
The two are two different registry states and must never be blurred.

CUT-PLATE fix round 1 (REVIEW F-1): the stale-prompt line is keyed to a real
BODY drift — a registered bootloader that differs from today's once the stamp
is normalized out (the compare Step 1.C writes on) — never to the stamp, which
Step 1.C deliberately leaves alone (BRIDGESIL1 §0.2).

TASKALARM1 — `dark_surfaces` is the dead-surface table, ALWAYS the current
truth: `task_alarm.dark_surfaces(ws, task_records=records, record=False)` — the
raw classification with the proactive render-once ledger never consulted and
never written (`record=False` is load-bearing: it is the escape hatch that keeps
an on-demand ask from being silenced by an earlier fire's own alarm). A health
check is an explicit ask, and it must answer with what is dark RIGHT NOW —
never with 'nothing to report' because the morning brief already alarmed this
same dead surface earlier today.

`health_verdict` (v4.5.2 R3) is the ONE verdict source: it runs the F-40
vantage guard, partitions every task into exactly one bucket (problem /
caught-up late / waiting on first run / on schedule), and computes
`summary_line` from that partition in code. Never recompute, restate, or
"round up" its counts in prose — the counts ARE the truth rules.

HYG1: the verdict also reads recent hard-failure records (a task that fired
and crashed mid-run — the failure was previously written and never read).
These arrive in `verdict["task_failures"]` + their `lines`, ride the
attention count, and never move a task out of its bucket. Render them
verbatim like every other verdict line — fact + the event's own quoted
diagnostic; the R3 cause-fabrication ban applies (never guess WHY it
failed). A failure older than the task's newest successful run is history
and the verdict already dropped it — don't resurrect it in prose.

## Step 3 — Render the answer

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "(3) a connector needs to be reconnected"
- GOOD: "(3) access to one of your tools — email, calendar, Slack — needs to be re-approved"

**Vantage blocked (`vantage` non-null — F-40, widened by TRUTH1):** render `summary_line` and STOP. This chat cannot see the scheduler that serves these chats, and `vantage['check']` says which of the three cases it is — the line is already composed for that case and is the only thing to say:

| `vantage['check']` | what it means | what the line already says |
|---|---|---|
| `registry_vantage` | a machine-local scheduler answered with nothing, on a seat that still has one | it points to a local (non-cloud) chat on the computer where the tasks run |
| `registry_vantage_cloud` | the account-level listing is empty and this workspace has registration history: the chats were set up in the older desktop app | it says they aren't visible here, and either how to move them or that Command Room will say when schedules are back |
| `scheduler_unreachable` | there is no scheduler tool in this session at all | the stopped sentence with the phrases to type — or, when a scheduled run is still landing, that another computer is still running them |

Render it verbatim and add nothing. NEVER report tasks as unregistered from a blind vantage, and NEVER name 'set up command room schedules' yourself — the line names it when, and only when, the backend's registration gate is actually open. The finding also carries a `machine` field: it is evidence for this step, never text — no state of this line prints it (F16).

**Everything healthy (empty `lines` + empty `info_lines`, no stale prompts, empty `dark_surfaces`):** render `summary_line` verbatim, then the task table below, and stop: it is the "Everything's running. All [N]…" one-liner, with the count computed from tasks that actually have on-schedule receipts.

**Anything else:** render `summary_line`, then every `info_lines` entry, then every `lines` entry — all verbatim, one per line (they're already plain English with dates and the fix named — never re-narrate with taskIds, cron strings, or event names).

**The task table (HEALTH3, F-T2-7).** When any entry of `reports` carries a `stamp_line`, those lines are this check's task table: render every `stamp_line` verbatim, one per line, in `reports` order, after the lines above (and after `summary_line` alone when everything is healthy). Each says when the app last ran that task and when it runs next, or that the app has not run it yet. Never compose a last-run time of your own, never read one out of any other field of `reports`, and never drop a task from the table.

**Another computer still writing (SAFETY0 — RETIRE1).** When this workspace has declared the one computer that runs its scheduled chats and another computer is still writing scheduled chats into it, the verdict already carries the one sentence (`task_watchdog.foreign_writer_count` → *"Another computer is still writing N scheduled chats into this workspace; delete them there or update Command Room on it."*) — inside `summary_line` when the scheduler is out of sight, as one of `lines` otherwise — so it renders verbatim with the rest; never name the other computer, its token or its account, and never add a sentence of your own about it.

**`dark_surfaces` (TASKALARM1) — the on-demand per-task verdict table.** When non-empty, render every entry's `line` verbatim, worst-first (the list already comes sorted `never_authorized` → `late` → `receipt_gap`) — the full table, uncapped and unfiltered by the proactive ledger, because this chat was asked directly. Never collapse it into `summary_line`'s count and never drop an entry because a proactive surface already said it once today — "check my schedules" answers with what is dark right now, full stop. A `never_authorized` entry always reads "was never set up on this machine"; a `late` or `receipt_gap` entry always reads "has stopped firing" / "hasn't recorded any work" — the two sentences must never be swapped or blended. Empty list → render nothing here (folded into "everything healthy" above).

**Truth rules (R3 — binding on this render, F-43/F-40/F-10):**
- A task only "ran on its normal schedule" if it has a run receipt on time — `summary_line` already encodes this; never widen its claim.
- A task named in ANY line (catch-up, first-run, problem) is already excluded from the on-schedule count — never re-add it or summarize the report as "everything's fine except…" with different numbers.
- Off-schedule catch-ups are reported with their dates (the info line carries them) — a task that caught up late is not "on schedule."
- Never state a CAUSE for a missing or late run. What is known is what the lines say; when a gap is unexplained, say what's known and stop.

When any `late` problem line renders, you may add the generic self-serve list below — it is framed as common possibilities, NOT a diagnosis of this task (grounded in the 2026-06 support calls; the watchdog cannot know which applies, and says so):

> *"I can't tell from here which applies, but the most common reasons a task stops running: (1) the computer was asleep or the lid closed when it was due — it runs at the next chance; (2) your Claude usage limit was reached — it resets on its own, check the usage meter; (3) access to one of your tools — email, calendar, Slack — needs to be re-approved: open the chat once and approve any prompt; (4) after a plugin update, the Claude app sometimes needs a full quit-and-reopen."*

If `stale_prompts` is non-empty — that list is `schedule_refresh.prompt_body_drift`, the task ids whose registered bootloader BODY differs from the one this plugin composes today once the diagnostic stamp is normalized out — add ONE line total — `schedule_refresh.stale_prompt_notice(drift)`, verbatim (CUT-PLATE, 2026-09-06 — the same sentence the update bridge and the Monday cleanup note say): *"Your scheduled chats are still running the setup from an older Command Room. Type `set up command room schedules` once and they'll be brought current — nothing else changes."* (The old wording sent the customer to `update command room`, which is the step that had already failed to reach the chats on the v5.28.0 attended test; the exact phrase that refreshes a registered prompt in place is `set up command room schedules`.) `stale_stamps` never earns this line: a stamp-only difference is not drift (fix round 1, REVIEW F-1 — Step 1.C leaves the stamp alone, so a stamp-keyed line would repeat on every check of every release whose bootloader body did not change, and the phrase could never clear it).

### Step 3b — full self-report scope (LB1, MANDATORY on a health check — FS-09)

A health check is the system's SELF-REPORT, not only a task-freshness readout. After the task lines, you MUST append these two lines (this is the LB1-grown scope the runtime skipped in the dogfood — it said "nothing needs your attention" over 77 open proposals). Time-of-day invariant; never drop them because the framing feels like a wrap-up.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "staff_meeting_helpers:health_substrate"}'
```

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"workspace_root": "<WS>"}, "name": "substrate_health:substrate_alarm_lines"}'
```

The first answer carries `integ` (`org_writer.count_failing_orgs(ws)`),
`n_waiting` (`len(load_open_proposals(ws, 'system-health'))` —
`brain_proposals`), `git_lint` (`substrate_health.check_git_in_drive(ws)` —
SYNC1 B4, the .git-in-Drive advisory), `n_new_duplicate_seqs` (`seq_health`'s
`detect_and_mark(ws)` read — BUG-8330 item 7c, READ-ONLY here, no --mark;
cleanup marks weekly), and `seq_gap`, the missing-entry report of
`detect_and_mark_gaps(ws)` — LEDGERFENCE1, READ-ONLY here too. The two
missing-entry lines are composed from that report by `seq_health` alone — the
one module that says them — and it reads no workspace, so it runs right here:

```python
import seq_health   # plugin code only: pure over the report handed back
seq_gap_line = seq_health.gap_notice(seq_gap)
seq_gap_standing = seq_health.gap_standing_line(seq_gap)
```

The second
answer IS the substrate alarm lines — `substrate_alarm_lines(ws)`, FS-04/05/06/15
+ SYNC1 stale-view, the loud ones. It comes through the WRITE door because it
sweeps resolved alerts on its way through (below); where the door cannot name
this seat's writer it answers `writer_identity_required`, and then the alarm
lines are the one part of the self-report that does not render. `alarms` is
that answer's list followed by `git_lint`.

- **Substrate alarms (FS-04/05/06/15 + SYNC1 — render FIRST, verbatim):** every line in `alarms` renders verbatim, most-severe first, ABOVE the rest of the self-report. These are the log-clobber, stale-view (SYNC1 A1 — the view is behind its own high-water mark), unreadable-JSON, and duplicate-entry alarms — LOUD by design (the dogfood found the readers degrading silently). `substrate_alarm_lines` also runs `alarm_artifacts.sweep_alerts` on the way through, so a resolved regression alert self-clears here rather than lingering (SYNC1 A2 — an alarm must not outlive its truth). The `check_git_in_drive` lines (SYNC1 B4) are advisory and render last. Empty → render nothing here.

### SYNC1 — substrate-sync doctrine (MANDATORY reads)

- **Never hand-author a substrate-regression alert or its recovery steps.** On a blocked substrate write (a fire that hits `SubstrateRegressionError`), the alert `.md` is rendered ONLY by `alarm_artifacts.write_alert(ws, marker)` FROM the `.seqregression.json` marker — never free prose. The 2026-07-19 fire hand-wrote an alert asserting data loss that was false by read-time; `write_alert` states BOTH hypotheses (stale mount vs real clobber) and never asserts loss as fact. If you find yourself typing recovery instructions, stop and call the helper.
- **Reconcile is automatic — surface it, don't drive it.** `reconcile_forward` runs inside the writer lock and self-heals quarantined batches once a healthy view returns (it writes a `substrate_reconciled` receipt). A health check SURFACES that the reconcile happened; it never re-runs recovery by hand.
- **Scheduled/maintenance fires preflight first.** Any write-chained scheduled fire calls `substrate_health.preflight_freshness(ws)` as step 0 and EXITS before any job runs when it returns `ok=False` (a stale mount is refused up front, jobs stay due, no quarantine litter) — see the maintenance prompt.
- **Entity-integrity line:** `unreadable=True` → surface a LOUD one-liner: *"⚠ I couldn't read your records file — it may be mid-sync or corrupted. If this persists, fully quit and reopen the Claude app."* (FS-15 — corruption is never silent.) Else `n_failing == 0` → *"Your records are clean ([n_orgs] orgs checked)."*; `n_failing > 0` → *"[n_failing] of [n_orgs] org records need repair — say `update command room` to fix them."*
- **Queue-health line:** `n_waiting > 0` → *"[n_waiting] items are waiting on you — say `staff meeting` to work through them."* When `n_waiting > 0` you may NOT claim the queue is empty or that nothing needs the CEO's attention — that was the FS-09 failure (an all-clear line rendered over 77 open proposals). Only `n_waiting == 0` renders the honest empty line: *"Nothing waiting on your eyes right now."*
- **Duplicate-seq line (BUG-8330 item 7c):** `n_new_duplicate_seqs > 0` → *"[n] activity-log entries share a record number — the weekly cleanup will mark them; new ones appearing means something is writing the log wrong."* Zero → render nothing (a marked historic duplicate is known, not news).
- **Missing-entry lines (LEDGERFENCE1 — CONTRACT Rule 31):** `seq_gap_line` non-empty → render it VERBATIM, every line of it (it is composed by `seq_health.gap_notice`, never re-worded here, so this surface and the Monday note say the same words). Empty string → render nothing (a marked historic stretch is known, not news). A duplicate number is two events written with the same number; a missing stretch is numbers that are not in the log. `scan_gaps` classifies every stretch on POSITIVE evidence only — the numbering visibly restarting, or a recorded repair that NAMES the numbers it set aside — and everything it cannot account for is a removal. `gap_notice` then says one plain sentence per class present: a removal first, then a repair, then a restarted numbering. Never drop a line because it sounds harmless, never re-word a line to say more than it says, never offer to put entries back, never suggest restoring a backup, and never edit the log to "tidy" it: this skill is read-only, and CONTRACT Rule 31 binds every other one.
- **The standing missing-entry line (LEDGERFENCE1 fix round 3 — this surface ONLY):** `seq_gap_standing` non-empty → render it VERBATIM, on its own line, right after `seq_gap_line`. Empty → render nothing. This is the ONE thing about a missing stretch that keeps being said after the news is spent: `gap_notice` says a stretch once and the weekly mark makes sure it is never said again, which is right for a restarted numbering and right for a repair, and wrong for entries nothing accounts for — a customer who was away on the one Monday it was said would otherwise never learn of it. `gap_standing_line` returns an empty string while `seq_gap_line` is still saying it, so the two are never both on screen. It is composed in `seq_health`, never re-worded here, and it lives on the health check ALONE: the Monday maintenance note stays once-only and the morning brief never sees any of it.

Then stop. No widget on the health check (the Staff Meeting surface below has its own render contract), no doc, no follow-up question.

## Step 4 — The self-report read ('what did you change' / 'what's waiting on me' — read-only)

Fires on exactly those two phrase families; the health check doesn't run unless also asked. Load both halves through the canonical readers — never hand-scan events.jsonl:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"since_iso": "<ISO of now - 24h, or the window the user asked for>", "workspace_root": "<WS>"}, "name": "staff_meeting_helpers:self_report"}'
```

The answer is `{feed, queue}` — `change_feed.changes_since(ws, <since>)`'s
lines and `brain_proposals.rank_proposals(load_open_proposals(ws,
'system-health'))`, both read where the data is.

Render, plain markdown, read-only:

- **'what did you change'** → the feed lines verbatim (each already carries its plain-English count and, where one applies, its undo affordance). Every line is traceable to an audit event — the feed is a READER; enforcement stays on the artifacts. Empty → *"Nothing since [window start] — no closures, no sweeps, nothing waiting."*
- **'what's waiting on me'** → the ranked open queue as numbered lines (each item's `render_line` or evidence, verbatim — never re-decide inclusion), ending with: *"Say `staff meeting` to work through these with one-tap actions."* Explicit asks see the full set of **adjudication** rows — deliberately exempt from the daily card's shown-marker dedup, which is why the projector call passes surface `system-health`.

  **One class is NOT in that set (STAFFCUT §3.7, M ruling 2026-08-02): dormancy.** Whether a quiet relationship has gone dormant is a judgment the CEO makes when he asks, so those rows are on-demand and every named surface — this one included — skips them. Nothing is hidden: `brain_proposals.load_open_proposals(ws, "on-demand")` returns them in full, which is the read the dont-forget chat performs. If the user asks specifically about quiet or dormant relationships, that is the call to make; do not fold them back into this count, and do not describe this list as "everything open".

No widget on this step, no shown-markers written, no resolution offered inline (the one-tap surface is the Staff Meeting). The Step 3 output guard applies verbatim.

## Step 5 — The Staff Meeting surface ('staff meeting' / 'run our staff meeting'; the scheduled chat wraps this)

The Living Brain's weekly review, fired on demand by those triggers or on schedule by the `staff-meeting` task (whose orchestrator — `references/orchestrator-staff-meeting.md` in the registration skill — executes THIS surface and owns the scheduled-fire mechanics: run-mode + lateness tiers, receipts, STOP contract). One surface, never forked. It renders, in order:

1. **"What I did on my own"** — `change_feed.changes_since(<last staff-meeting receipt ts, else 7d>)`, ≤3 lines, drop-empty.
2. **"What's waiting on you"** — the ranked queue, **grouped and BOUNDED by the driver (STAFFCUT 2026-08-02)**. `brain_proposals.load_open_proposals(ws, "staff-meeting")` still returns the full projection (the R2 full-set exemption from daily dedup is unchanged), and the driver then runs two render-side passes over it: `proposal_digests.group_into_digests` (one row per evidence class, carrying every member's own id and dispatch payload) and `proposal_digests.bound_page` (about two screens for the whole page, appended sections included, budget split across the shapes present so no lane starves). One measured fire before this was 105 rows over 7 screens. Paginated as design with the canonical `show more` verb, never a size fallback. **Do NOT reconstruct the "complete" render** — the honest full totals ride the section titles, which the builder writes.
3. **RETIRED FROM THIS SURFACE (WRAPSTAFF1 4.7, 2026-09-17).** "This week's moves" used to render here as a third section. Ruling 6 of 2026-09-03 places it on the Friday wrap, not on the Staff Meeting, and the 09-15 fire carried it anyway (ATTENDED_TEST_v5.31.0 B2.7). **Do not compute, render or emit relationship moves on this surface** — no `compute_relationship_moves` call, no `live_contact_check` pass, no `--moves-json`. The machinery is untouched and answers on demand through `skills/relationship-moves/SKILL.md` ("who should I reach out to"), which owns the MUST-run live-contact check and the 7-day exclusion window. **The wrap does not carry a moves section and is not getting one: M ruled on 2026-09-17 that "this week's moves" adds NOTHING to the wrap, and that the on-demand skill is what ruling 6's "on demand" means.** Do not read this as saying the wrap has one, and do not build one.

**Load the widget tool before anything else** (`shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first): on the merged and half-merged seats it is deferred, and a listed deferred tool counts as present; ToolSearch loads it, its setup tool runs first, then the relay.

**Do NOT build the data view yourself — the driver does, and since STAFFCUT a hand-built one is WRONG in two ways at once.** This step used to print a `build_card_view(queue, …, header=f"Staff Meeting — {len(queue)} waiting on you")` recipe; following it now skips both render passes (restoring the unbounded 7-screen page) and hard-codes a header count against the pre-digest queue length, breaking the RV-4 rule that the header must equal the rows the widget SHOWS. The canonical builder is `surface_drivers.build_staff_meeting_view`, reached through the ONE driver call below, which runs the projector, the digest pass, the bound, `brain_proposals.build_card_view` (with the section-title honest totals) and `widget_transport.render_and_persist` internally. It produces the MONEY / IDENTITY / HYGIENE sections with honest counts and per-row registered verbs — never hand-assemble sections and never invent a bulk verb (FS-10); a digest row is the driver's, not yours to compose. Batch Apply-all + the standard undo affordance ("Say `undo` to reverse this." — `brain_undo.undo_batch`, additive only). Transport: ONE driver call through the WRITE door — the `run_writer` form below. `staff_meeting_helpers:run_staff_meeting_surface` is `surface_drivers.run_surface("staff-meeting", …)`, the function `surface_drivers.py staff-meeting --workspace … --page 1 --page-size 10 --fired-via manual` has always called (`fired_via: "manual"` is `--fired-via manual`, `page` is `--page`, `page_size` is `--page-size`); it runs exactly this build + `widget_transport.render_and_persist` internally (the renderer's full validator chain fires inside the call) AND writes the `staff-meeting` `pack_run` receipt inside the same invocation (FB-7: `receipts.log_receipt` at the one chokepoint the scheduled orchestrator also fires with its detected run mode, so both paths receipt identically). It runs where the workspace is, never in this chat's own shell: in a shell that holds no workspace the old command line drew an EMPTY card and receipted it, a false all-clear (ORCH2 fix pass 1). **The scheduled `staff-meeting` fire never runs this line** — its orchestrator's Phase 5 carries the same call with the run mode it detected. Relay the answer's `html` to `mcp__visualize__show_widget` as `widget_code` (the same bytes the command line printed between the `CR-WIDGET-HTML-BEGIN`/`END` markers) — never hand-composed HTML; `show more` re-fires the same form with `page` N+1 (pages 2+ never re-receipt). Empty queue → say so honestly in two lines, no widget, still log the receipt via the canonical helper: `receipts.log_receipt(WORKSPACE_ROOT, "staff-meeting", fired_via="manual", surfaced=0)`.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"fired_via": "manual", "page": <N, 1 on the first page>, "page_size": 10, "workspace_root": "<WS>"}, "name": "staff_meeting_helpers:run_staff_meeting_surface"}'
```

The answer is the transport: `html`, `pagination`, `receipt` (`"status": "written"`) and `page_rel`, the audit page named workspace-relative. A `{refused: "mount_stale", lines}` answer means the folder has not finished syncing: say `lines` as the whole answer and stop — nothing was rendered and nothing was written.

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

## Deferred / already-shipped adjacent surfaces (context, not Step 5)

The Tue/Thu quiet-chased-tail sweep shipped Phase 4 (2026-07-02) as the daily commitment orchestrator's Phase 3.8 (originally titled "WAITING ON"; renamed **"NUDGED — NO REPLY"** by CTS1 §4.1 when the whole chat took the Waiting On name) — its gates (the kinds split + counterparty receipts) merged, and it rides the daily chat's own task (`waiting-on` post-CTS1; `commitments` pre-split), so nothing new registered. Two opt-in surfaces remain gated until this watchdog has been live long enough to verify fires: the day-1/week-1 lifecycle one-shots (cut in v4.1.0 because nothing could verify they fired) and an optional "system health" sidebar card. Each ships as its own later addition through the normal add paths — this skill only diagnoses.

## Gotchas

- **Machine-local time is the scheduler's clock.** Cron evaluates in the machine's timezone, not the workspace timezone — the watchdog's math is machine-local by design. Don't "correct" a fire time against the workspace TZ; `tz.py` localization is presentation-only.
- **A later-add task rendering as "not registered" is not a failure** — relationship-moves / commitments / commitment-triage / staff-meeting are deliberately not first-install. The watchdog stays quiet about them; change-schedule owns that render.
- **A fire can be on schedule and still half-failed (SPEC BRIEFMERGE §D).** The morning-brief fire runs TWO legs — meeting prep first, then the digest — and writes ONE receipt carrying both. `health_verdict` folds `legs` findings in the same way it folds maintenance job findings: they ride the problem lines and the attention count, and they never move the task out of its bucket, because the brief genuinely did run. "Your Morning Brief ran, but meeting prep didn't" is a real finding with a real fix; report it and say nothing when both legs ran. A receipt with no leg fields is a pre-BRIEFMERGE fire — silence, not a finding.
- **Skipped is not failed, quiet is not broken, and waiting is not failed (T2 FIRE1, FIX-7).** A prep leg recorded as `skipped` is a decision the fire made — a typed `morning briefing` never runs the prep leg, so its receipt always says `skipped` — and it is never reported as prep failing. A quiet maintenance job (Old Questions Answered, Memory Coverage Check, the schedule realign) writes nothing on a day with nothing to do, so its missing receipt is never "hasn't recorded any work". Meeting Capture left waiting by the maintenance fire is waiting, not failed: `health_verdict` says the one waiting sentence in its `info_lines`. Only the verdict's own `lines` name a problem; never read the receipts yourself to find one.
- **A RETIRED task is quiet too, and for a different reason** — membership is `schedule_config.RETIRED_TASKS` (`pulse`, eliminated by SPEC LIFECYCLE1; `upcoming-meetings`, merged into the morning brief by SPEC BRIEFMERGE; `past-meetings`, RENAMED to `end-of-day` by SPEC EOD2), never a name list here. A later-add is absent because the workspace hasn't added it YET; a retired task is absent because it is GONE. Both are silent, and the retired class stays silent PERMANENTLY: never report its absence, never propose adding it, never count it as a missing chat in any completeness check. If a workspace still has one REGISTERED, that is not a health finding either — the retirement offer belongs to the update bridge (propose, never silent), not to the watchdog. This is structural, not discipline: a retired id is out of `DEFAULT_SCHEDULES`, and `check_tasks` reports only what the merged config carries.
- **A still-registered `past-meetings` is a HEALTHY End of Day chat, and the watchdog must never say otherwise (SPEC EOD2).** The rename is propose-only, so most of the fleet will run the predecessor id for a long time — possibly forever, since nothing degrades. Two halves make that silence correct rather than lucky, and both are in code: `check_tasks` answers "is `end-of-day` registered?" and "when did it last fire?" over the SERVING set (`schedule_config.renamed_predecessors`), so a machine with only `past-meetings` reports End of Day as registered and on schedule; and `plain_english_lines` renders that row under the name the customer's Scheduled list shows (`served_by`), so a genuine finding about that chat says "Past Meetings" to a customer whose sidebar says Past Meetings. Never report `end-of-day` as "missing from the schedule" on a workspace that has the predecessor — the fix that sentence recommends (`set up command room schedules`) is fenced from doing it, so the customer would be sent in a circle.
- **Don't fabricate a diagnosis.** If the watchdog returns a finding you can't explain, surface the finding and the named fix — never speculate about causes beyond the generic self-serve list (which is possibilities, not a diagnosis). "The computer was likely asleep" as an asserted cause is the exact fabricated-narrative class the 2026-07 dogfood catalogued (F-10/F-43/F-47).
- **An empty scheduler list is a vantage question before it is a finding.** `health_verdict` checks the substrate's registration history first (F-40); trust its `vantage` verdict. A cloud/remote chat reading an empty machine-local registry and reporting "nothing is registered" is the false total-outage failure this skill exists to never repeat.

## Narration leak scan (CUT-C item 8 — MANDATORY on every composed line)

Widget bodies are scanned inside `widget_transport.render_and_persist`; the PROSE this skill composes around them is not, unless this step runs. Before posting any sentence you composed — an ack, a header, a summary, a pointer, a "why" line — run `validate_chat_output(<the text>)` from `chat_output_renderer.py` (`shared/scripts/`). It raises `LeakDetectedError` on a raw id (`person_NNN`, `project_NNN`, `org_NNN`, a `cmt_` / `bp_` / `pcand:` wire id), an event or field name, a file name or path, or a score. ABORT the post and rewrite the sentence with the entity's name (`narration_names.humanize(text, narration_names.name_index(<WORKSPACE>))` is the one substitution). NEVER catch the error and post anyway. Text relayed byte-exact from a driver or the transport is already scanned and is not re-composed.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> On-demand scheduled-task health check — the reliability watchdog's interactive surface (Phase 3 / W1, 2026-07) — and the system's full self-report (LB1). Reads each scheduled task's substrate receipts + the live scheduler records, compares against expected cadence in machine-local time, and answers in plain English: what's running, what stopped, what was never authorized, and the one action that fixes each. Triggers: 'is everything running', 'system health', 'health check', 'are my tasks running', 'did my tasks run', 'why didn't my [task] run'. ('health check' used to redirect to cleanup — it moved here in Phase 3; cleanup still runs the same watchdog weekly as its deep pass.) LB1 self-report triggers: 'what did you change' / 'what changed since [when]' / 'what have you been doing' → the change feed (Step 4); 'what's waiting on me' / 'what needs my eyes' / 'anything waiting on me' → the pending queue (Step 4); 'staff meeting' / 'run our staff meeting' / 'staff meeting now' → the full Staff Meeting surface (Step 5 — queue + feed, one-tap actions). The health check is read-only: it never registers, re-registers, or edits anything — it diagnoses and names the fix; the Staff Meeting surface writes only its own receipt + the transport-persisted widget. DOES NOT fire on 'weekly cleanup' / 'tidy up' / 'maintenance' (cleanup), 'set up command room schedules' (registration), 'change my schedule' / 'add staff meeting' (change-schedule), 'usage report' (usage-report), 'prep me for the staff meeting' / 'prep me for' (call-prep), or 'process the staff meeting notes' / 'process the meeting' / 'meeting notes' (meeting-notes). An upcoming calendar staff meeting is a meeting to prep; a transcript of one is a meeting to process — neither is this surface. The staff-meeting chat's registration and cadence flow through the change-schedule add path, never inline from here.
