---
name: system-health
description: "On-demand scheduled-task health check AND the system's self-report. Fires on: 'health check', 'is everything running', 'system health', 'are my tasks running', 'did my tasks run', 'why didn't my [task] run', 'what did you change', 'what's waiting on me', 'staff meeting', 'run our staff meeting' (the full Staff Meeting surface — pending-proposal queue + change feed, same as the weekly chat). Reads run receipts, scheduler records, the proposal queue, and the change feed; answers in plain English what's running, what changed, and what's waiting on you. Health check is read-only — names the fix, never registers or edits. Does NOT fire on 'weekly cleanup' / 'maintenance' (cleanup), 'set up command room schedules' (registration), 'change my schedule' / 'add staff meeting' (change-schedule), 'usage report' (usage-report), 'prep me for [meeting]' (call-prep), or 'process the meeting' (meeting-notes). Full corpus + Staff Meeting contract: Routing section in the body."
---

# system-health

The on-demand face of the scheduled-task watchdog (`shared/scripts/task_watchdog.py`) — and, as of LB1, the system's full self-report: not just "is everything running" but "what did you change" and "what's waiting on me". The CEO asks and gets a straight answer grounded in artifacts — substrate receipts, scheduler records, the Living Brain's audit trail — never in what a past fire narrated (the Bug #98 lesson, generalized). The same watchdog rides the morning brief (light daily pass) and cleanup's Monday note (weekly deep pass); this skill exists so the customer never has to wait for either.

## Skill Boundary (v2.1; scope grown LB1)

- **Owns:** the on-demand health diagnosis of Command Room's scheduled tasks + workspace binding; the on-demand change-feed and pending-queue read (Steps 4); the Staff Meeting surface (Step 5 — the scheduled `staff-meeting` chat's orchestrator wraps THIS surface).
- **Does not own:** fixing anything. Registration/repair is `set up command room schedules`; cadence changes are `change-schedule`; weekly maintenance is `cleanup`; usage/cost telemetry is `usage-report`. Resolutions on queue items belong to apply-choices → each item's own writer.

## Writer Contract

The health check (Steps 1–3) and the self-report read (Step 4) are read-only — no events, no config, no files. The Staff Meeting surface (Step 5) writes exactly three things, all through canonical helpers: the widget file persisted by `widget_transport.render_and_persist` itself, the `staff-meeting` fire receipt via `receipts.log_receipt`, and the `relationship_move_suggested` events appended by `compute_relationship_moves(emit=True)` — that emission is load-bearing (it IS the 7-day dedupe against a still-registered standalone relationship-moves chat), never disable it. Nothing else — every mutation on a queue item happens later, through apply-choices dispatch, on the user's click.

## Step 1 — Gather the two evidence sources

1. `mcp__scheduled-tasks__list_scheduled_tasks` — the live scheduler records (taskId, lastRunAt, enabled, prompt).
2. The workspace substrate — resolve `[WORKSPACE_ROOT]` via the canonical CONTRACT.md Rule 22 discovery preamble.

## Step 2 — Run the watchdog

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -dt "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null | head -1 | sed 's:/$::')
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import task_watchdog as tw
ws = '<workspace_root>'
records = <the list_scheduled_tasks result, as a Python list of task dicts>
verdict = tw.health_verdict(ws, task_records=records)
installed = json.loads(open('.claude-plugin/plugin.json', encoding='utf-8').read()).get('version', '')
drift = tw.check_prompt_versions(records, installed)
print(json.dumps({
    'vantage': verdict['vantage'],
    'summary_line': verdict['summary_line'],
    'lines': verdict['lines'],
    'info_lines': verdict['info_lines'],
    'reports': verdict['reports'],
    'stale_prompts': [f for f in drift if f.get('stale')],
}))
"
```

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

**Vantage blocked (`vantage` non-null — F-40):** render `summary_line` and STOP. This chat cannot see the machine-local scheduler (cloud/remote session, or a different computer); the line already says so and points to a local chat. NEVER report tasks as unregistered, NEVER name 'set up command room schedules' from here — running it in this chat would register everything into the wrong place and create duplicates.

**Everything healthy (empty `lines` + empty `info_lines`, no stale prompts):** render `summary_line` verbatim and stop — it is the "Everything's running. All [N]…" one-liner, with the count computed from tasks that actually have on-schedule receipts.

**Anything else:** render `summary_line`, then every `info_lines` entry, then every `lines` entry — all verbatim, one per line (they're already plain English with dates and the fix named — never re-narrate with taskIds, cron strings, or event names).

**Truth rules (R3 — binding on this render, F-43/F-40/F-10):**
- A task only "ran on its normal schedule" if it has a run receipt on time — `summary_line` already encodes this; never widen its claim.
- A task named in ANY line (catch-up, first-run, problem) is already excluded from the on-schedule count — never re-add it or summarize the report as "everything's fine except…" with different numbers.
- Off-schedule catch-ups are reported with their dates (the info line carries them) — a task that caught up late is not "on schedule."
- Never state a CAUSE for a missing or late run. What is known is what the lines say; when a gap is unexplained, say what's known and stop.

When any `late` problem line renders, you may add the generic self-serve list below — it is framed as common possibilities, NOT a diagnosis of this task (grounded in the 2026-06 support calls; the watchdog cannot know which applies, and says so):

> *"I can't tell from here which applies, but the most common reasons a task stops running: (1) the computer was asleep or the lid closed when it was due — it runs at the next chance; (2) your Claude usage limit was reached — it resets on its own, check the usage meter; (3) access to one of your tools — email, calendar, Slack — needs to be re-approved: open the chat once and approve any prompt; (4) after a plugin update, Cowork sometimes needs a full quit-and-reopen."*

If `stale_prompts` is non-empty, add ONE line total: *"Your scheduled chats were set up under an older version — say 'update command room' once and they'll refresh themselves."*

### Step 3b — full self-report scope (LB1, MANDATORY on a health check — FS-09)

A health check is the system's SELF-REPORT, not only a task-freshness readout. After the task lines, you MUST append these two lines (this is the LB1-grown scope the runtime skipped in the dogfood — it said "nothing needs your attention" over 77 open proposals). Time-of-day invariant; never drop them because the framing feels like a wrap-up.

```bash
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from org_writer import count_failing_orgs
from brain_proposals import load_open_proposals
from substrate_health import substrate_alarm_lines, check_git_in_drive
ws = '<workspace_root>'
integ = count_failing_orgs(ws)
n_waiting = len(load_open_proposals(ws, 'system-health'))
alarms = substrate_alarm_lines(ws)   # FS-04/05/06/15 + SYNC1 stale-view — loud substrate alarms
git_lint = check_git_in_drive(ws)    # SYNC1 B4 — .git-in-Drive advisory
print(json.dumps({'integ': integ, 'n_waiting': n_waiting, 'alarms': alarms + git_lint}))
"
```

- **Substrate alarms (FS-04/05/06/15 + SYNC1 — render FIRST, verbatim):** every line in `alarms` renders verbatim, most-severe first, ABOVE the rest of the self-report. These are the log-clobber, stale-view (SYNC1 A1 — the view is behind its own high-water mark), unreadable-JSON, and duplicate-entry alarms — LOUD by design (the dogfood found the readers degrading silently). `substrate_alarm_lines` also runs `alarm_artifacts.sweep_alerts` on the way through, so a resolved regression alert self-clears here rather than lingering (SYNC1 A2 — an alarm must not outlive its truth). The `check_git_in_drive` lines (SYNC1 B4) are advisory and render last. Empty → render nothing here.

### SYNC1 — substrate-sync doctrine (MANDATORY reads)

- **Never hand-author a substrate-regression alert or its recovery steps.** On a blocked substrate write (a fire that hits `SubstrateRegressionError`), the alert `.md` is rendered ONLY by `alarm_artifacts.write_alert(ws, marker)` FROM the `.seqregression.json` marker — never free prose. The 2026-07-19 fire hand-wrote an alert asserting data loss that was false by read-time; `write_alert` states BOTH hypotheses (stale mount vs real clobber) and never asserts loss as fact. If you find yourself typing recovery instructions, stop and call the helper.
- **Reconcile is automatic — surface it, don't drive it.** `reconcile_forward` runs inside the writer lock and self-heals quarantined batches once a healthy view returns (it writes a `substrate_reconciled` receipt). A health check SURFACES that the reconcile happened; it never re-runs recovery by hand.
- **Scheduled/maintenance fires preflight first.** Any write-chained scheduled fire calls `substrate_health.preflight_freshness(ws)` as step 0 and EXITS before any job runs when it returns `ok=False` (a stale mount is refused up front, jobs stay due, no quarantine litter) — see the maintenance prompt.
- **Entity-integrity line:** `unreadable=True` → surface a LOUD one-liner: *"⚠ I couldn't read your records file — it may be mid-sync or corrupted. If this persists, fully quit and reopen Cowork."* (FS-15 — corruption is never silent.) Else `n_failing == 0` → *"Your records are clean ([n_orgs] orgs checked)."*; `n_failing > 0` → *"[n_failing] of [n_orgs] org records need repair — say `update command room` to fix them."*
- **Queue-health line:** `n_waiting > 0` → *"[n_waiting] items are waiting on you — say `staff meeting` to work through them."* When `n_waiting > 0` you may NOT claim the queue is empty or that nothing needs the CEO's attention — that was the FS-09 failure (an all-clear line rendered over 77 open proposals). Only `n_waiting == 0` renders the honest empty line: *"Nothing waiting on your eyes right now."*

Then stop. No widget on the health check (the Staff Meeting surface below has its own render contract), no doc, no follow-up question.

## Step 4 — The self-report read ('what did you change' / 'what's waiting on me' — read-only)

Fires on exactly those two phrase families; the health check doesn't run unless also asked. Load both halves through the canonical readers — never hand-scan events.jsonl:

```bash
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from change_feed import changes_since
from brain_proposals import load_open_proposals, rank_proposals
ws = '<workspace_root>'
feed = changes_since(ws, '<ISO of now - 24h, or the window the user asked for>')
queue = rank_proposals(load_open_proposals(ws, 'system-health'))
print(json.dumps({'feed': feed['lines'], 'queue': queue}, default=str))
"
```

Render, plain markdown, read-only:

- **'what did you change'** → the feed lines verbatim (each already carries its plain-English count and, where one applies, its undo affordance). Every line is traceable to an audit event — the feed is a READER; enforcement stays on the artifacts. Empty → *"Nothing since [window start] — no closures, no sweeps, nothing waiting."*
- **'what's waiting on me'** → the ranked open queue as numbered lines (each item's `render_line` or evidence, verbatim — never re-decide inclusion), ending with: *"Say `staff meeting` to work through these with one-tap actions."* Explicit asks see the full set of **adjudication** rows — deliberately exempt from the daily card's shown-marker dedup, which is why the projector call passes surface `system-health`.

  **One class is NOT in that set (STAFFCUT §3.7, M ruling 2026-08-02): dormancy.** Whether a quiet relationship has gone dormant is a judgment the CEO makes when he asks, so those rows are on-demand and every named surface — this one included — skips them. Nothing is hidden: `brain_proposals.load_open_proposals(ws, "on-demand")` returns them in full, which is the read the dont-forget chat performs. If the user asks specifically about quiet or dormant relationships, that is the call to make; do not fold them back into this count, and do not describe this list as "everything open".

No widget on this step, no shown-markers written, no resolution offered inline (the one-tap surface is the Staff Meeting). The Step 3 output guard applies verbatim.

## Step 5 — The Staff Meeting surface ('staff meeting' / 'run our staff meeting'; the scheduled chat wraps this)

The Living Brain's weekly review, fired on demand by those triggers or on schedule by the `staff-meeting` task (whose orchestrator — `references/orchestrator-staff-meeting.md` in the registration skill — executes THIS surface and owns the scheduled-fire mechanics: run-mode + lateness tiers, receipts, STOP contract). One surface, never forked. It renders, in order:

1. **"What I did on my own"** — `change_feed.changes_since(<last staff-meeting receipt ts, else 7d>)`, ≤3 lines, drop-empty.
2. **"What's waiting on you"** — the ranked queue, **grouped and BOUNDED by the driver (STAFFCUT 2026-08-02)**. `brain_proposals.load_open_proposals(ws, "staff-meeting")` still returns the full projection (the R2 full-set exemption from daily dedup is unchanged), and the driver then runs two render-side passes over it: `proposal_digests.group_into_digests` (one row per evidence class, carrying every member's own id and dispatch payload) and `proposal_digests.bound_page` (about two screens for the whole page, appended sections included, budget split across the shapes present so no lane starves). One measured fire before this was 105 rows over 7 screens. Paginated as design with the canonical `show more` verb, never a size fallback. **Do NOT reconstruct the "complete" render** — the honest full totals ride the section titles, which the builder writes.
3. **"This week's moves"** — the relationship-moves machinery rendered as a section (R4): `relationship_moves.compute_relationship_moves(ws, top_n=3, thread_totals={})` after the MANDATORY per-candidate `live_contact_check` (inherit the dormant-customer-scan MUST-language verbatim — no dormancy-driven outreach from substrate-only data), openers via the email-writer chain exactly as `skills/relationship-moves/SKILL.md` specifies. Reuse, never fork — the machinery's own 7-day exclusion keeps this section and any still-registered standalone Relationship Moves chat from double-suggesting.

**Do NOT build the data view yourself — the driver does, and since STAFFCUT a hand-built one is WRONG in two ways at once.** This step used to print a `build_card_view(queue, …, header=f"Staff Meeting — {len(queue)} waiting on you")` recipe; following it now skips both render passes (restoring the unbounded 7-screen page) and hard-codes a header count against the pre-digest queue length, breaking the RV-4 rule that the header must equal the rows the widget SHOWS. The canonical builder is `surface_drivers.build_staff_meeting_view`, reached through the ONE driver call below, which runs the projector, the digest pass, the bound, `brain_proposals.build_card_view` (with the section-title honest totals) and `widget_transport.render_and_persist` internally. It produces the MONEY / IDENTITY / HYGIENE sections with honest counts and per-row registered verbs — never hand-assemble sections and never invent a bulk verb (FS-10); a digest row is the driver's, not yours to compose. Batch Apply-all + the standard undo affordance ("Say `undo` to reverse this." — `brain_undo.undo_batch`, additive only). Transport: ONE driver call — `python3 shared/scripts/surface_drivers.py staff-meeting --workspace "<WORKSPACE>" --page 1 --page-size 10 --fired-via manual [--moves-json <temp file>]` — which runs exactly this build + `widget_transport.render_and_persist` internally (the renderer's full validator chain fires inside the call) AND writes the `staff-meeting` `pack_run` receipt inside the same invocation (FB-7: `receipts.log_receipt` at the one chokepoint the scheduled orchestrator also fires with its detected run mode, so both paths receipt identically). Relay the bytes between the `CR-WIDGET-HTML-BEGIN`/`END` markers to `mcp__visualize__show_widget` as `widget_code` — never hand-composed HTML; `show more` re-fires the driver with `--page N+1` (pages 2+ never re-receipt). Empty queue AND empty moves → say so honestly in two lines, no widget, still log the receipt via the canonical helper: `receipts.log_receipt(WORKSPACE_ROOT, "staff-meeting", fired_via="manual", surfaced=0)`.

## Deferred / already-shipped adjacent surfaces (context, not Step 5)

The Tue/Thu quiet-chased-tail sweep shipped Phase 4 (2026-07-02) as the daily commitment orchestrator's Phase 3.8 (originally titled "WAITING ON"; renamed **"NUDGED — NO REPLY"** by CTS1 §4.1 when the whole chat took the Waiting On name) — its gates (the kinds split + counterparty receipts) merged, and it rides the daily chat's own task (`waiting-on` post-CTS1; `commitments` pre-split), so nothing new registered. Two opt-in surfaces remain gated until this watchdog has been live long enough to verify fires: the day-1/week-1 lifecycle one-shots (cut in v4.1.0 because nothing could verify they fired) and an optional "system health" sidebar card. Each ships as its own later addition through the normal add paths — this skill only diagnoses.

## Gotchas

- **Machine-local time is the scheduler's clock.** Cron evaluates in the machine's timezone, not the workspace timezone — the watchdog's math is machine-local by design. Don't "correct" a fire time against the workspace TZ; `tz.py` localization is presentation-only.
- **A later-add task rendering as "not registered" is not a failure** — relationship-moves / commitments / pulse / commitment-triage / staff-meeting are deliberately not first-install. The watchdog stays quiet about them; change-schedule owns that render.
- **Don't fabricate a diagnosis.** If the watchdog returns a finding you can't explain, surface the finding and the named fix — never speculate about causes beyond the generic self-serve list (which is possibilities, not a diagnosis). "The computer was likely asleep" as an asserted cause is the exact fabricated-narrative class the 2026-07 dogfood catalogued (F-10/F-43/F-47).
- **An empty scheduler list is a vantage question before it is a finding.** `health_verdict` checks the substrate's registration history first (F-40); trust its `vantage` verdict. A cloud/remote chat reading an empty machine-local registry and reporting "nothing is registered" is the false total-outage failure this skill exists to never repeat.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> On-demand scheduled-task health check — the reliability watchdog's interactive surface (Phase 3 / W1, 2026-07) — and the system's full self-report (LB1). Reads each scheduled task's substrate receipts + the live scheduler records, compares against expected cadence in machine-local time, and answers in plain English: what's running, what stopped, what was never authorized, and the one action that fixes each. Triggers: 'is everything running', 'system health', 'health check', 'are my tasks running', 'did my tasks run', 'why didn't my [task] run'. ('health check' used to redirect to cleanup — it moved here in Phase 3; cleanup still runs the same watchdog weekly as its deep pass.) LB1 self-report triggers: 'what did you change' / 'what changed since [when]' / 'what have you been doing' → the change feed (Step 4); 'what's waiting on me' / 'what needs my eyes' / 'anything waiting on me' → the pending queue (Step 4); 'staff meeting' / 'run our staff meeting' / 'staff meeting now' → the full Staff Meeting surface (Step 5 — queue + feed + this week's moves, one-tap actions). The health check is read-only: it never registers, re-registers, or edits anything — it diagnoses and names the fix; the Staff Meeting surface writes only its own receipt + the transport-persisted widget. DOES NOT fire on 'weekly cleanup' / 'tidy up' / 'maintenance' (cleanup), 'set up command room schedules' (registration), 'change my schedule' / 'show my schedule' / 'add staff meeting' (change-schedule), 'usage report' (usage-report), 'prep me for the staff meeting' / 'prep me for' (call-prep), or 'process the staff meeting notes' / 'process the meeting' / 'meeting notes' (meeting-notes). An upcoming calendar staff meeting is a meeting to prep; a transcript of one is a meeting to process — neither is this surface. The staff-meeting chat's registration and cadence flow through the change-schedule add path, never inline from here.
