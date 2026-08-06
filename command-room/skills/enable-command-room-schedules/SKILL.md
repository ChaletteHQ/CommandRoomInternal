---
name: enable-command-room-schedules
surfaces: cowork
slack_fallback: "Scheduled chats are registered from desktop Cowork — ask there; your Slack briefs are scheduled server-side and fire on their own."
description: "Sets up Command Room's scheduled chats and silent background tasks — registration is the writer of record for the schedule config. Fires on: 'set up command room schedules', 'register my scheduled chats', 'set up my daily chats', and silently from the update bridge. Registers the daily action chats, the weekly surfaces, and one maintenance task carrying the SILENT_TASKS jobs, each loading its steps fresh from the installed plugin at fire time. Proposes optional client-mix tasks (relationship-moves, dormant-customer-scan) — propose, never auto-register. Does NOT fire on 'change my schedule' / 'show my schedule' / 'pause [chat]' (change-schedule — the user-facing mutator). Registration mechanics, task set, and verify mode: Routing section in the body."
---

## Verify-only mode (preview without firing) — rewritten for the bootloader era (Phase 3 / P0.3)

Triggers: `verify command room prompts` / `check my command room version` / `which version are my tasks on`

When fired with one of these phrases, this skill runs in **read-only verification mode** — it inspects the registered prompts WITHOUT updating anything. Output: per-task status. Use this BEFORE firing scheduled tasks if you want to confirm the prompts are current.

**What changed in Phase 3 (P0.3):** the old flow checked markers where they no longer live and classified every HEALTHY install as "unknown / very old" (see references/HISTORY.md § Phase 3 / P0.3). Verification now checks each layer where that layer actually lives (bootloaders intentionally don't carry the OUTPUT CONTRACT marker; the contract lives in the on-disk orchestrator files the bootloader reads at fire time):

1. **Read the installed plugin version** from `$PLUGIN_ROOT/.claude-plugin/plugin.json` (resolve `$PLUGIN_ROOT` via the canonical CONTRACT.md Rule 22 preamble: `SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"`).
2. Call `mcp__scheduled-tasks__list_scheduled_tasks`. The Command Room set is every registered taskId that appears in `ORCHESTRATOR_MAP` (the chats) or the `SILENT_TASKS` registry (the background tasks) — bare taskIds, up to the full DEFAULT_SCHEDULES set (currently 10). Do NOT filter on a `cr-` prefix (retired v2.14.27; matches zero tasks on a current install).
3. **Chat tasks — verify the registered prompt is a canonical bootloader:** check the same `REQUIRED_MARKERS` Phase 3.5 uses (`# Scheduled task bootloader`, `Resolve the plugin path`, `Read the orchestrator and execute it verbatim`, `Anti-improvisation contract`) plus correct `<TASK_ID>`/orchestrator-filename substitution and no leading frontmatter. A prompt missing the markers is a stub or a pre-bootloader pin → "refresh needed".
4. **Chat tasks — read the registration version stamp:** parse `plugin-version:` from the registered prompt (stamped at registration, Phase 3/W4). Stamp == installed version → current. Stamp older → "registered under vX — refresh will land on the next `set up command room schedules` run". No stamp → "pre-stamp registration (older than Phase 3)" — informational, not a failure, because fire behavior always comes from the freshly-resolved plugin.
5. **Contract layer — read from the FILES, not the prompts:** for each taskId in `ORCHESTRATOR_MAP`, read the on-disk `references/orchestrator-<name>.md` and confirm it carries the `OUTPUT CONTRACT` marker in its first 1500 chars (same assertion Step 1.A applies at registration). That is where the contract lives in the bootloader era; a registered prompt was never the right place to look for it.
6. **Silent tasks:** verify the registered prompt matches the current composed prompt from `compose_silent_task_prompt()` (hash compare) — match → current; differ → "refresh needed"; missing → "not registered — say `set up command room schedules`".

   Display format:

   ```
   Command Room scheduled-task verification:

   Plugin: v4.4.0 installed.

   ✓ upcoming-meetings       bootloader current (registered under v4.4.0)
   ✓ inbox                   bootloader current (registered under v4.4.0)
   ✓ pulse                   bootloader current (pre-stamp registration)
   ✗ past-meetings           bootloader stale — refresh needed
   ✓ cleanup                 background prompt current
   ✗ weekly-insights         not registered

   Orchestrator files on disk: all carry the current contract.

   5 of 7 current. Say `set up command room schedules` to refresh the rest.
   ```

7. If EVERYTHING is current, confirm in one line: `All [N] tasks current under plugin v[X] — run any task with confidence.` If ANY are stale or missing, the report ends with the explicit instruction to run the refresh trigger.

**Do NOT update any prompt during verify-only mode.** This is observability, not mutation.

Verification mode is the diagnostic version of the install ritual — explicit visibility into which prompts are current BEFORE fire-and-find-out. (For fired-recency — "did they actually RUN" — that's the watchdog: say `system health`.)

---

# enable-command-room-schedules (M1, 2026-05-23)

The schedule-setup skill. Configures **7 topic-specific persistent chats** + a one-time **historical backfill** sweep via `mcp__scheduled-tasks__create_scheduled_task`. Each chat = 1 stable taskId = 1 persistent thread in Cowork's Scheduled sidebar section, accumulating turns over time.

**On a fresh-install workspace, only 5 of the 7 fire automatically** — `morning-brief`, `past-meetings`, `inbox`, `upcoming-meetings`, `friday-wrap`. The remaining 2 (`waiting-on`, `my-plate`) get added later via operator-driven follow-up sessions when accumulated workspace signal makes them useful. (`pulse` was the third until LIFECYCLE1 retired it — see the RETIRED row in `ORCHESTRATOR_MAP`.) (CTS1: `waiting-on` + `my-plate` are the split successors of the retired `commitments` chat — an existing customer with `commitments` registered gets both via the Phase 1 migration table, never a fresh-install auto-add.)

## Phase 0.5 — Substantive explainer (first-time schedule setup)

When this skill fires the **first time the customer sets up their schedules**, surface the substantive vanilla-vs-Command-Room explainer below BEFORE Phase 0 workspace-discovery runs. Because Phase 0.C's full `FIRST_INSTALL` detection hasn't run yet at this point, use this **lightweight pre-check** to decide: call `mcp__scheduled-tasks__list_scheduled_tasks` and check whether ANY Command Room taskId (any key of `ORCHESTRATOR_MAP` or the `SILENT_TASKS` registry, or any legacy `cr-*` id) is registered. None registered → treat as first-time and show the explainer. Any registered → skip it (Phase 0.C later makes the authoritative first-install call for registration purposes). This is the education beat that gives the customer the "why" behind the 5 they're about to authorize.

> **Decoupled from onboarding (Command Room build, 2026-06).** Onboarding no longer opens a parallel "Chat 2" to fire this skill — scheduled-task generation was stripped from onboarding. The customer now reaches this skill by running `set up command room schedules` in a fresh chat whenever they're ready (onboarding's Phase 6 points them here). So this explainer fires on that first opt-in, regardless of whether onboarding is complete — it is no longer gated on an onboarding parent context.

Skip this phase if the skill fires from `command-room-update-bridge` post-install (silent registration; no customer in the chat), from a re-run (`FIRST_INSTALL = False` per Phase 0.C detection — customer already knows), or from any explicit calibration trigger (`change my schedule`, etc.).

Read `workspace.brain_name` from the customer's entities.json if available — substitute for `[BrainName]` below. If not yet set (entities.json not seeded yet because onboarding is still mid-flight), default to "Penelope."

**Surface this verbatim (one chat message):**

> *"Scheduled tasks are how [BrainName] reaches out to you — she starts the conversation instead of you having to remember to ask. Each one is a chat that runs on its own schedule and produces output you read like any other conversation. The chat appears in your sidebar under 'Scheduled.'*
>
> *You can set up scheduled tasks in Cowork without Command Room. The reason they're significantly more useful with Command Room is everything [BrainName] reads when she runs one.*
>
> ***A vanilla scheduled task starts cold.*** *It asks the AI to do something with no memory of who you are. Each run starts from zero — you'd have to re-explain your business, your people, your priorities every time.*
>
> ***A Command Room scheduled task starts with full context loaded.*** *[BrainName] walks into every run knowing: who you are, the companies and people in your workspace, your writing patterns (learned from your sent emails), every decision you've logged, every commitment captured from your calls and emails, and the current state of each of your projects.*
>
> ***Practical difference.*** *A vanilla morning brief gives you a generic 'here's your calendar' rundown. Your Command Room morning brief gives you 'you have [Person] at 2pm — he hasn't sent you anything in 28 days, you owe him the Q2 review since Wednesday, here's the opening line that lands hardest given your last 3 conversations.' Same prompt, completely different output, because [BrainName] is reading from everything she already knows about your business.*
>
> ***The compounding effect.*** *Every meeting you process, every decision you log, every follow-up you send adds to what she knows. The longer you use Command Room, the more context exists, the sharper every scheduled task gets.*
>
> ***Future possibilities.*** *I'm setting up 5 scheduled tasks for you now — these cover the daily ritual. Later you can add more yourself or with [Operator]. Examples: a Monday morning prep specifically for your weekly [recurring 1:1] / first-of-the-month investor update draft / pre-call brief that runs 30 min before any meeting with [important person]. One more you can switch on any time through your schedule settings: a **monthly KPI scorecard** — a one-page how-are-we-tracking-against-targets read that lands on the 1st for the prior month, off until you ask for it. [BrainName] can also propose tasks based on patterns she notices.*

**Opt-in monthly scorecard job (SPEC OUT7 — PROPOSE, never auto-register).** The `monthly-scorecard` job lives in `maintenance_dispatcher.OPTIONAL_JOBS` and is OFF by default — this skill NEVER registers it as part of the first-install set (it is not in `FIRST_INSTALL_TASK_IDS` and rides inside the already-authorized `maintenance` task, so turning it on needs no new Run Now). Offer it once, in plain English, as above; on an explicit yes, turn it on the same way change-schedule does — write `workspace.schedule_config.maintenance_jobs.monthly-scorecard = {"enabled": true}` via the atomic entities write, then confirm in one line. Absent that explicit confirmation it stays inert (the dispatcher never surfaces it as due). It renders the prior month's KPI scorecard via `shared/scripts/scorecard.py` at the first maintenance fire on/after the 1st and self-limits to monthly via its own `pack_run` receipt.
>
> ***Registering your 5 chats now:***
>
> *• Upcoming Meetings (6:30 AM weekdays)*
> *• Morning Brief (7 AM weekdays)*
> *• Inbox (7:15 AM weekdays)*
> *• Past Meetings (5 PM weekdays)*
> *• Friday Wrap (1 PM Fridays)*
>
> *These times are the defaults — say `change my schedule` to move any of them.*
>
> *...registering...*
> *All 5 registered. They appear in your Cowork 'Scheduled' section now and will run on their own on the cadence above. Want to see one in action right now? Open any of them and hit Run Now — you'll get real output immediately, exactly what it'll produce on schedule."*

**Customer-facing task-name vs registered taskId mapping.** The customer reads the 5 DISPLAY names above — the same names the Cowork sidebar shows. The actual registered taskIds are: `morning-brief` ("Morning Brief - Command Room") / `past-meetings` ("Past Meetings - Command Room") / `inbox` ("Inbox - Command Room") / `upcoming-meetings` ("Upcoming Meetings - Command Room") / `friday-wrap` ("Friday Wrap - Command Room"). Never surface a taskId or internal skill ID (`inbox-triage`, `weekly-recap`) in customer copy; the canonical taskIds stay back-compat-stable in the registration layer only.

**Render the explainer fire-times FROM `load_schedule_config()` — do not trust the hardcoded copy.** The bullet list above shows the current `DEFAULT_SCHEDULES` values (`shared/scripts/schedule_config.py`) for reference, but before speaking them, call `load_schedule_config(entities_json_path)` and read each task's `label` (e.g. `"7 AM weekdays"`) so the times you state ALWAYS match what Phase 2 actually registers — including any per-workspace `schedule_config` overrides the operator has already set. This is the anti-drift contract: the explainer copy and the registered cron can never disagree because both come from the same source. (Pre-FIX1 hand-maintained copy drifted — see references/HISTORY.md § Pre-FIX1.)

**OPERATOR (verbal, if present when the explainer lands):** *"Take a minute on that. The 'full context loaded' point is the most important thing here — it's why this stack is different from any of the AI tools you've tried. Anything jump out?"*

~60–90 sec of optional operator-customer discussion. When the customer is setting schedules up on their own (the common case post-onboarding), no operator cue is needed — they read the explainer, the 5 register, and they can Run Now any of them to see output immediately.

After this phase, proceed to Phase 0 (workspace discovery).

---

## v2.10.2 changes from v2.9.x / v2.10.1

Task renames, the commitments merge, the historical-backfill chunk tasks, and the chat-output-rules move are recorded in references/HISTORY.md § v2.10.2. The operative legacy→current taskId mapping lives in the Phase 1 migration table below.

## Verified contract (Cowork investigation, 2026-04-28)

- API: `create_scheduled_task(taskId, prompt, description, cronExpression?, fireAt?, recurrence?, notifyOnCompletion?)`
- Cron: 5-field, **LOCAL time** (not UTC). Step+range syntax supported.
- One-shot: `recurrence: "once"` with `fireAt: "<ISO>"` instead of `cronExpression`. Used for historical backfill chunks.
- Prompt: arbitrary chat string, NOT a skill trigger. Each fire = fresh Claude session.
- Jitter: 60-400 second deterministic dispatch jitter.
- Persistence: filesystem + events.jsonl only. No in-memory state across runs.
- 1 taskId = 1 persistent chat thread in Cowork's Scheduled section. Each fire appends a turn to that thread.
- **One-time install ritual:** every new taskId blocks on a manual tool-permission grant on first fire. The customer clicks Run Now once per task to authorize. Subsequent fires autonomous.

## Phase 0 — Workspace discovery + customer confirmation (v2.14.26+)

**Why this phase exists:** the Cowork diagnostic 2026-05-06 confirmed `userSelectedFolders` is NOT a passable parameter to `create_scheduled_task` / `update_scheduled_task` — passing one is silently dropped. Folder binding is implicit at fire time. v2.14.26 works around this by baking the customer-confirmed workspace folder's basename into each task's bootloader at registration time.

This phase runs FIRST, before any task registration. It produces the `WORKSPACE_BASENAME` value used in Phase 1's bootloader composition.

**Step 0.A — Discover candidate workspaces.**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
echo "SESSION_DIR=$SESSION_DIR"
find "$SESSION_DIR/mnt" -maxdepth 5 -type f -name "events.jsonl" -path "*/_hq/data/*" 2>/dev/null | while read f; do
  WS=$(dirname "$(dirname "$(dirname "$f")")")
  BASENAME=$(basename "$WS")
  MTIME=$(stat -c "%y" "$f" | cut -d'.' -f1)
  EVENTS_COUNT=$(wc -l < "$f")
  echo "CANDIDATE basename=$BASENAME path=$WS last_event=$MTIME events_count=$EVENTS_COUNT"
done
```

Each `CANDIDATE` line is a workspace the customer could bind their tasks to. The basename is what gets baked into each bootloader; the full path + last-event timestamp + events count help the customer decide which is which (the more recently-active workspace is almost always the right one).

**Step 0.B — Customer confirmation flow.**

Three branches based on candidate count:

- **0 candidates:** no workspace folder is connected to Cowork right now (or none has the canonical `_hq/data/events.jsonl` layout). Surface plain English and abort registration:

  > *"I can't find a Command Room workspace folder connected to this Cowork session. Make sure your workspace folder is connected in Cowork's Settings → Folders, then say `set up command room schedules` again."*

  Do NOT register tasks. The bootloaders would have nothing to bind to.

- **1 candidate:** show + confirm:

  > *"I'll set your scheduled chats up on this workspace: `<workspace folder name>` (at `<full_path>`, last activity <MTIME>). Look right? Say `yes`, or paste a different folder path if this isn't the one."*

  Wait for the reply — there is no timeout in a chat turn. If the reply is anything other than a different path or an objection, treat it as confirmation and proceed. Save `WORKSPACE_BASENAME = <BASENAME>` for Phase 1.B substitution (the "workspace folder name" shown to the customer IS the basename — show it as a plain folder name, never labeled "basename", and never show raw event counts or `_hq/...` paths).

- **2+ candidates:** show numbered list, ask customer to pick:

  > *"I see <N> Command Room workspaces connected to Cowork. Which one should your scheduled chats use?*
  >
  > *1) `<workspace folder name 1>` — `<full_path1>` (last activity <MTIME1>)*
  > *2) `<workspace folder name 2>` — `<full_path2>` (last activity <MTIME2>)*
  > *...*
  >
  > *Reply with `1`, `2`, etc. — or paste a different folder path."*

  Store the customer's choice as `WORKSPACE_BASENAME` for Phase 1.B substitution.

**Step 0.C — Detect first install vs re-run + persist the choice.**

**First-install detection (onboarding-v2 / 2026-05-17+):** read `<chosen_workspace>/_hq/workspace_config.json`. The workspace is treated as **first-install** if any of:

- File doesn't exist.
- File exists but `registered_taskIds` is missing, null, or `[]`.

If first-install, set the local variable `FIRST_INSTALL = True` and use `FIRST_INSTALL_TASK_IDS` (from `shared/scripts/schedule_config.py` — `{"morning-brief", "upcoming-meetings", "past-meetings", "inbox", "friday-wrap"}` as of M1 2026-05-23; pre-M1 set was 4 tasks with `inbox` deferred) as the registration set. The 2 remaining default tasks (`commitments`, `pulse`) are SKIPPED on first install — they get added later through operator-led follow-up sessions once accumulated workspace signal makes them useful.

If NOT first-install (`registered_taskIds` is populated), set `FIRST_INSTALL = False`. The skill enters Phase 6 management flow (`add` / `change` / `remove` / `reset`) — do NOT silently delete or disable tasks the customer already has. Existing customers with all 5, 6, or 7 registered keep what they have.

After detection, write or update `<chosen_workspace>/_hq/workspace_config.json`:

```json
{
  "workspace_root": "<absolute path>",
  "workspace_basename": "<BASENAME>",
  "registered_at": "<ISO timestamp>",
  "first_install": true,
  "registered_taskIds": ["morning-brief", "upcoming-meetings", "past-meetings", "inbox", "friday-wrap"]
}
```

For re-runs, `registered_taskIds` reflects the actual set the skill ended up registering at the end of Phase 3 (preserves whatever existed + adds anything new). The `first_install` flag is set true ONLY on the first registration; subsequent re-runs leave it true for audit history but Phase 3 reads `len(registered_taskIds_before_this_run)` to decide first-install vs not.

Future re-runs read this file first; if present, pre-select that workspace and only ask for confirmation if a different workspace is now connected. Idempotent.

**Step 0.D — Switching workspaces (lifecycle command).**

The customer-facing flow for switching to a new workspace is just: re-run `set up command room schedules`. The skill detects the new candidate set, asks the customer to confirm or pick a different workspace, re-bakes the basename into each bootloader, and re-registers via `update_scheduled_task`. No separate "rebind" command needed — the regular setup command IS the rebind command. Surface this guidance in the install summary at the end.

## Phase 1 — Detect current schedule state + migrate legacy

`mcp__scheduled-tasks__list_scheduled_tasks`. Build set of `{taskId, cron, prompt}`.

**Vantage guard — BLOCKING, before any registration (v4.5.2 R3 — F-40):** the scheduler registry is MACHINE-LOCAL. If the list comes back with zero Command Room tasks, run `task_watchdog.detect_registry_vantage(ws, records)` (plugin `shared/scripts/`) before treating this as a fresh install. If it returns a finding, the workspace's own records say schedules were already set up and (usually) running — an empty registry here means this chat cannot see that scheduler: a cloud/remote session, or a second computer. Registering now would create a duplicate task set in the wrong place (the F-38 double-fire class; in a cloud session the registrations land in a throwaway VM). So STOP and say, in substance: *"Your workspace shows Command Room schedules already set up[, with runs as recent as [time]], but I can't see that scheduler from this chat — this looks like a cloud session or a different computer. If you want a full check or a re-register, open a local (non-cloud) chat on the computer where Command Room runs. If you're deliberately setting up a SECOND computer, tell me that's what you want and I'll continue."* Proceed only on that explicit in-chat confirmation (per-machine second setups are a real, supported case — the sweep needs one) — never silently.

**Legacy task migration (v2.9-v2.10.1 → v2.10.2):**

For each legacy taskId found in the user's existing schedule, DISABLE it via `update_scheduled_task(enabled: false)` and surface in the install summary as "migrated to [new name]":

| Legacy taskId | Action | New taskId |
|---|---|---|
| `cr-meetings-today` | disable + register | `upcoming-meetings` |
| `cr-inbox-pulse` | disable + register | `inbox` |
| `cr-commitment-nudge` | disable + register | `commitments` (merged) |
| `cr-commitment-chase` | disable + register | `commitments` (merged) |
| `cr-cracks-watch` | **disable only** (LIFECYCLE1 — `pulse`, its successor, is retired; there is nothing to register) | — |
| `cr-meetings-processed` | disable + register | `past-meetings` |
| `cr-refresh-workspace-map` | **disable** (v2.14.25 — task removed from active set; surface "Removed the daily Workspace Map auto-refresh — the ↻ Refresh button on your Workspace Map still works") | (none — task is gone) |
| `cr-upcoming-meetings` | **disable** (v2.14.27 — taskId rename; old "Cr upcoming meetings" title replaced by clean "Upcoming meetings" via new `upcoming-meetings` taskId) | `upcoming-meetings` |
| `cr-inbox` | **disable** (v2.14.27 — taskId rename) | `inbox` |
| `cr-commitments` | **disable** (v2.14.27 — taskId rename) | `commitments` |
| `cr-dont-forget` | **disable** (v2.14.27 — taskId rename to align with display name "Pulse"; events.jsonl history at source_skill='cr-dont-forget' preserved as append-only history) | `pulse` |
| `cr-past-meetings` | **disable** (v2.14.27 — taskId rename) | `past-meetings` |
| `cr-folder-bind-test` | **disable** (v2.14.27 — Cowork diagnostic test task left over from 2026-05-06 Q10/Q11 round; safe to disable, never intended to fire) | (none — diagnostic artifact) |
| `cr-folder-bind-test-2` | **disable** (v2.14.27 — Cowork diagnostic test task left over from 2026-05-06 Q10/Q11 round; safe to disable, never intended to fire) | (none — diagnostic artifact) |
| `commitments` | **disable + register** (CTS1 §10.3, RULED 2026-07-16 — the daily Commitments chat split into two surfaces on fresh taskIds, the v2.14.27 pattern: Cowork derives the sidebar title FROM the taskId, so re-scoping would have left the sidebar saying "Commitments" forever. Register BOTH successors: `waiting-on` inherits the 8:30 slot AND any custom cron override the customer had on `commitments` — MOVE the override (write it under `waiting-on` in `workspace.schedule_config` and REMOVE the `commitments` key: `commitments` is no longer in DEFAULT_SCHEDULES, so a leftover key trips the watchdog's orphan-override scan forever); `my-plate` takes the 8:45 default. Surface the three §10.3 costs in the install summary: the old entry stays visible as a disabled sidebar item (no delete API), old chat history stays in the old thread, and each new chat needs one first-fire Run Now.) | `waiting-on` + `my-plate` |
| `cleanup` | disable + register (MAINT1 — now a job inside the maintenance task; driven by `SUPERSEDED_BY` in `schedule_config.py`, data not prose) | `maintenance` |
| `reconcile-sent` | disable + register (MAINT1 — now the FIRST job at the 6:45 slot, still before the morning brief. A custom cron override on this taskId migrates onto the `maintenance` task cron — it was the one old silent task whose cadence maps 1:1) | `maintenance` |
| `monthly-report` | disable + register (MAINT1 — now a job, due at the first fire on/after the 1st) | `maintenance` |
| `weekly-insights` | disable + register (MAINT1 — now a job at the Sunday slot, ordered after cleanup) | `maintenance` |
| `session-sweep` | disable + register (MAINT1 — now a job, served once daily at the first fire) | `maintenance` |

**MAINT1 migration notes (idempotent, never deletes):** the five rows above are driven by the `SUPERSEDED_BY` map in `shared/scripts/schedule_config.py` — disable each superseded taskId found registered+enabled via `update_scheduled_task(enabled: false)`, register `maintenance` once via Step 1.D. Re-runs converge on the same end state (disabling an already-disabled task is a no-op; a registered `maintenance` with a matching prompt is skipped). Custom cron overrides on the OTHER four old taskIds (`workspace.schedule_config`) cannot map onto a single task cron — leave the override in place (parity ignores superseded ids) and tell the customer in plain English which old time can't carry over (e.g. *"Your cleanup used to run at a custom time — the background upkeep now runs as one task; say `change my schedule` if you want to move it."*). Surface ONE plain-English migration line in the install summary: *"Your background upkeep now runs as one 'Maintenance' entry in the Scheduled section — authorize it once with Run Now. The old background entries are switched off."*

(No delete API exists in the scheduled-tasks MCP; disable is the safe operation. Disabled tasks remain in the user's Scheduled section as historical reference but won't fire. v2.14.27 customers running `set up command room schedules` will see ~13 disabled tasks accumulate in their sidebar — surface this in the install summary so it's not a surprise. Filesystem surgery is the only way to make the sidebar truly clean: quit Cowork, edit `scheduled-tasks.json` to remove disabled entries, optionally delete the corresponding `Documents/Claude/Scheduled/<taskId>/` folders, restart.)

**Existing-taskId handling (v2.14.21+ — self-refresh with explicit verification):**

The canonical taskId → orchestrator-file mapping. **Use this dict literally — do NOT improvise filenames or display names from your own knowledge of the task list.** This dict MIRRORS `references/orchestrator-map.json`, which is the single machine-readable source of truth — the Step 1.A registration snippet below reads that same JSON, so the two can never drift. If you ever edit one, edit the JSON:

```python
ORCHESTRATOR_MAP = {
    "morning-brief":     "orchestrator-morning-brief.md",  # Wraps the morning-briefing skill. Registered on first install.
    "upcoming-meetings": "orchestrator-upcoming-meetings.md",
    "inbox":             "orchestrator-inbox.md",
    "waiting-on":        "orchestrator-commitments.md",  # CTS1 Surface 1 — the re-scoped daily (things people owe the user + the confirm tail). Filename kept for events.jsonl source_skill back-compat (events keep source_skill='commitments' — same pattern as pulse below). NOT first-install; successor of the retired `commitments` taskId (Phase 1 migration table).
    "my-plate":          "orchestrator-my-plate.md",     # CTS1 Surface 2 — the owner-me act-list (Promised + Personal groups, one chat). NOT first-install; registers alongside waiting-on in the commitments migration.
    "pulse":             "orchestrator-dont-forget.md",   # RETIRED (LIFECYCLE1) — NEVER register, NEVER offer, NEVER count as missing. The row stays ONLY so a workspace that registered it before the retirement still resolves its bootloader; the file it points at is a retirement stub that explains itself and stops. `schedule_config.RETIRED_TASKS` is the membership test — never a name you remember. Historical events (source_skill='cr-dont-forget' / 'pulse') stay valid append-only history.
    "past-meetings":     "orchestrator-past-meetings.md",
    "friday-wrap":       "orchestrator-friday-wrap.md",   # NEW v3.11.0. Wraps the weekly-recap skill. Registered on first install. First weekly-rhythm task.
    "relationship-moves": "orchestrator-relationship-moves.md",  # REL1 — weekly Sunday outreach pack. NOT first-install (needs accumulated substrate).
    "commitment-triage": "orchestrator-commitment-triage.md",  # Phase 2 Stage D (S4) — weekly Friday housekeeping chat. NOT first-install. (Row restored to this mirror 2026-07-14 — the JSON had it, the dict had drifted.)
    "staff-meeting":     "orchestrator-staff-meeting.md",  # LB1 R3 — weekly Monday Living Brain review. NOT first-install; propose-only later-add (never silently registered).
    "balance":           "orchestrator-balance.md",  # BAL1 — weekly Sunday-8AM personal white-space surface (m_facing only). NOT first-install; opt-in later-add gated on a declared personal calendar (workspace.personal_calendars) — with none declared the fire refuses honestly.
    "pipeline-digest":   "orchestrator-pipeline-digest.md",  # PIPE1 Part 2 — weekly Tuesday-8AM deal review (movement since last digest + the pipeline report + top-3 moves). NOT first-install; proposed only when >=1 open tracked deal exists (schedule_proposals); adjudication of deal suggestions stays in the Staff Meeting (FB-20).
}
# Thirteen tasks total — all user-facing chats. v2.14.27+ uses bare taskIds (no `cr-` prefix) so Cowork's sidebar title formatting renders cleanly: `inbox` → "Inbox", `waiting-on` → "Waiting on", etc. Pre-v2.14.27 used `cr-*` prefix which displayed as "Cr inbox" / "Cr commitments" — the cr- prefix looked like a typo in the title. cr-refresh-workspace-map was REMOVED in v2.14.25. friday-wrap ADDED in v3.11.0 — first weekly-rhythm scheduled task. CTS1: `commitments` RETIRED (disable per the Phase 1 migration table) and split into `waiting-on` + `my-plate`.
#
# First-install gating: on a FRESH workspace (workspace_config.json missing or empty registered_taskIds), only the subset in `shared/scripts/schedule_config.py FIRST_INSTALL_TASK_IDS` registers ({morning-brief, upcoming-meetings, past-meetings, inbox, friday-wrap} as of M1 2026-05-23; inbox added in M1 — pre-M1 was 4 tasks). The remaining later-add entries above (waiting-on, my-plate, pulse) stay in the map for re-runs / management flows but are NOT auto-registered day 1. See Phase 3 first-install branching. (The silent background work — the `maintenance` task, MAINT1 — is ALSO in FIRST_INSTALL_TASK_IDS and registers on first install, but via **Step 1.D below** as one loop over the `SILENT_TASKS` registry in `shared/scripts/schedule_config.py`, because it is not a chat-orchestrator and is intentionally absent from this ORCHESTRATOR_MAP.)
```

**Critical mismatch warnings:**

- **(v2.14.20 regression)** If you find a registered task with `taskId == "cr-pulse"` or `cr-dont-forget`, that's pre-v2.14.27 state — disable per the legacy migration table and register `pulse` per the new map.
- **(v2.14.27 rename)** If you find any of `cr-upcoming-meetings`, `cr-inbox`, `cr-commitments`, `cr-dont-forget`, or `cr-past-meetings` registered, those are v2.14.21-v2.14.26 taskIds — disable per the legacy migration table and register the bare-name equivalents (`upcoming-meetings`, `inbox`, etc.) fresh.
- **(stub regression)** If any registered task's prompt body is shorter than 1000 chars or doesn't contain `"OUTPUT CONTRACT (v2.13.0+ — MANDATORY)"` in its first 1500 characters AND doesn't contain `"# Scheduled task bootloader"` in its first 200 chars — that indicates a stub was registered instead of the canonical bootloader. Re-register fresh.

**Per-task processing (v2.14.24+ — bootloader pattern; run for every taskId in `ORCHESTRATOR_MAP`):**

**v2.14.24 architecture change.** Prior to v2.14.24, registration pinned the FULL canonical orchestrator body into Cowork's scheduled-tasks DB — fixing the v2.14.20 stub-improvisation bug but producing the stale-prompt drift bug (see references/HISTORY.md § v2.14.24).

v2.14.24+ pins a tiny ~50-line **bootloader** instead. The bootloader resolves `$PLUGIN_ROOT` at fire time, reads the canonical `orchestrator-<name>.md` from the currently-installed plugin via `bash cat`, and executes it verbatim. Plugin upgrades propagate automatically. Drift is structurally impossible.

The canonical bootloader template lives at `skills/enable-command-room-schedules/references/scheduled-task-bootloader.md`. Read its top-of-file commentary for the full design rationale — the live evidence (cr-bootloader-test fire, plugin UUID stability check, frontmatter doubling test) that drove each design choice.

**Step 1.A — Verify orchestrator files exist and carry the contract marker (still mandatory at registration time, even though the body itself is no longer pinned).**

The bootloader assumes the orchestrator files exist on disk and contain the OUTPUT CONTRACT marker. Registration verifies this BEFORE composing the bootloader, so a partial / corrupt plugin install can't quietly register bootloaders that will fail at every fire:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
cd "$PLUGIN_ROOT" && python3 -c "
import json
from pathlib import Path
ref_dir = Path('skills/enable-command-room-schedules/references')
# Single source of truth — same JSON the prose ORCHESTRATOR_MAP above mirrors.
# Strip the _comment key; everything else is taskId -> orchestrator filename.
ORCHESTRATOR_MAP = {k: v for k, v in json.loads((ref_dir / 'orchestrator-map.json').read_text(encoding='utf-8')).items() if not k.startswith('_')}
CONTRACT_MARKER = 'OUTPUT CONTRACT (v2.13.0+ — MANDATORY)'
out = {}
for task_id, fname in ORCHESTRATOR_MAP.items():
    fpath = ref_dir / fname
    assert fpath.exists(), f'{fname} missing — plugin install may be incomplete; ABORT registration'
    body = fpath.read_text(encoding='utf-8')
    # All 7 tasks are chat-emitting; all must carry the v2.13.0 OUTPUT CONTRACT preamble.
    assert CONTRACT_MARKER in body[:1500], f'{fname} is missing the v2.13.0 OUTPUT CONTRACT preamble in its first 1500 chars — file is stale or corrupt; ABORT registration'
    assert len(body) >= 1500, f'{fname} body is only {len(body)} chars — too short to be a real orchestrator; ABORT'
    out[task_id] = len(body)
print('orchestrator-files-verified:', json.dumps(out))
"
```

If any assertion fails, ABORT the whole skill with a plain-English error: *"One of the Command Room files isn't readable right now. Reinstalling Command Room usually fixes this — ask whoever set it up for you, or reinstall it the same way it was first installed, then say `set up command room schedules` again."* Never register a partial set.

**Step 1.B — Compose each task's bootloader by substituting placeholders into the template.**

Three placeholders are substituted: `<TASK_ID>`, `<ORCHESTRATOR_FILENAME>`, and (v2.14.26+) `<WORKSPACE_BASENAME>` from Phase 0's customer-confirmed workspace choice. Pass `WORKSPACE_BASENAME` as an environment variable into the Python invocation:

```bash
WORKSPACE_BASENAME="<from Phase 0 — customer-confirmed basename>"
cd "$PLUGIN_ROOT" && WORKSPACE_BASENAME="$WORKSPACE_BASENAME" python3 -c "
import json, os
from pathlib import Path
ORCHESTRATOR_MAP = {
    'morning-brief': 'orchestrator-morning-brief.md',
    'upcoming-meetings': 'orchestrator-upcoming-meetings.md',
    'inbox': 'orchestrator-inbox.md',
    'waiting-on': 'orchestrator-commitments.md',  # CTS1 — filename kept for source_skill back-compat
    'my-plate': 'orchestrator-my-plate.md',       # CTS1
    'pulse': 'orchestrator-dont-forget.md',  # RETIRED (LIFECYCLE1) — resolvable, never registered
    'past-meetings': 'orchestrator-past-meetings.md',
    'friday-wrap': 'orchestrator-friday-wrap.md',  # NEW v3.11.0 — weekly recap
    'relationship-moves': 'orchestrator-relationship-moves.md',  # REL1 — weekly Sunday outreach
}
workspace_basename = os.environ.get('WORKSPACE_BASENAME', '').strip()
assert workspace_basename, 'WORKSPACE_BASENAME env var not set — Phase 0 did not run or did not surface a customer choice; ABORT'
assert '/' not in workspace_basename and '\\\\' not in workspace_basename, f'WORKSPACE_BASENAME must be a basename (no path separators), got: {workspace_basename!r}; ABORT'
# Phase 3 / W4 (2026-07) — version stamp for watchdog drift detection. Diagnostic only:
# fire behavior always comes from the freshly-resolved plugin; the stamp lets the
# watchdog detect a bootloader registered under an old plugin version.
plugin_version = json.loads(Path('.claude-plugin/plugin.json').read_text(encoding='utf-8')).get('version', '')
assert plugin_version, 'plugin.json has no version field — plugin install incomplete; ABORT'
ref_dir = Path('skills/enable-command-room-schedules/references')
template_path = ref_dir / 'scheduled-task-bootloader.md'
assert template_path.exists(), 'scheduled-task-bootloader.md missing — plugin install incomplete; ABORT'
full_template = template_path.read_text(encoding='utf-8')
# Strip the design-rationale preamble; the registered body starts at the marker line.
marker = '## The bootloader template (everything below this heading is the registered prompt body)'
assert marker in full_template, 'bootloader template missing canonical-marker line — file may be corrupt; ABORT'
template_body = full_template.split(marker, 1)[1].lstrip('\\n').lstrip()
bootloaders = {}
for task_id, fname in ORCHESTRATOR_MAP.items():
    body = (template_body
            .replace('<TASK_ID>', task_id)
            .replace('<ORCHESTRATOR_FILENAME>', fname)
            .replace('<WORKSPACE_BASENAME>', workspace_basename)
            .replace('<PLUGIN_VERSION>', plugin_version))
    # Hard sanity checks — never register a bootloader with unsubstituted placeholders.
    assert '<TASK_ID>' not in body, f'{task_id} bootloader has unsubstituted <TASK_ID> placeholder; ABORT'
    assert '<ORCHESTRATOR_FILENAME>' not in body, f'{task_id} bootloader has unsubstituted <ORCHESTRATOR_FILENAME> placeholder; ABORT'
    assert '<WORKSPACE_BASENAME>' not in body, f'{task_id} bootloader has unsubstituted <WORKSPACE_BASENAME> placeholder; ABORT'
    assert '<PLUGIN_VERSION>' not in body, f'{task_id} bootloader has unsubstituted <PLUGIN_VERSION> placeholder; ABORT'
    # Frontmatter rule (Cowork prepends its own — user-supplied frontmatter creates a doubling bug)
    assert not body.lstrip().startswith('---'), f'{task_id} bootloader starts with frontmatter — Cowork will double; ABORT'
    bootloaders[task_id] = body
print('bootloaders-composed:', json.dumps({tid: len(b) for tid, b in bootloaders.items()}))
print(f'workspace-bound-to: {workspace_basename}')
"
```

The composed bootloader for each task is what gets passed as the `prompt` parameter to `create_scheduled_task` / `update_scheduled_task`. NOT the orchestrator body. The orchestrator body is read fresh by the bootloader at every fire. Each task's bootloader contains the customer-confirmed workspace basename in its Step 1 path-resolution logic.

**Step 1.C — For each `taskId`, hash the composed bootloader and compare against the prompt currently registered in Cowork's scheduled-tasks DB** (returned by `list_scheduled_tasks`). Three outcomes:

- **No registered taskId** → call `create_scheduled_task(taskId, prompt=bootloader_body, ...)`. Pass the FULL composed bootloader string as the `prompt` parameter.
- **Existing taskId with matching hash** → skip. Idempotent.
- **Existing taskId with different hash** → call `update_scheduled_task(taskId, prompt=bootloader_body)`. Preserve `cronExpression`, `description`, `enabled`, `notifyOnCompletion` — only the prompt updates.

**Surface in the install summary:** `Migrated N tasks to bootloader pattern (plugin upgrades will now auto-propagate)` for v2.14.24-from-prior migrations, or `All 7 bootloaders already current` if nothing changed (post-v3.11.0; pre-v3.11.0 was 6).

**Why this matters (v2.14.24 architecture).** The bootloader closes the drift bug structurally — fires read from disk fresh every time, so a plugin upgrade is enough to update fire behavior (lineage: v2.14.20 → v2.14.21 → v2.14.24 in references/HISTORY.md). **Customers no longer have to re-run `set up command room schedules` after every plugin upgrade.** The hard read happens at fire time, not at registration time.

**Step 1.D — Register the silent maintenance tasks (SPEC-2.3 registry loop — the registry currently holds ONE task, `maintenance`, and any future silent task registers through the same loop).**

The silent background tasks are NOT chat-orchestrators (no widget, no `orchestrator-*.md`) — they are skill-invoking prompts registered separately from the chats. As of Phase 3 (2026-07) they are **data-driven from the `SILENT_TASKS` registry in `shared/scripts/schedule_config.py`** — one loop registers all of them, and a silent task added to that registry in a future release registers here with zero edits to this file. (Pre-registry, each task had its own prose block, and each block was a place to forget one — Bug #82 was exactly that miss; see references/HISTORY.md § Bug #82 silent-task registration miss.)

As of MAINT1 (2026-07) the registry holds exactly one task: `maintenance` (`45 6,12,17 * * *` daily). It carries the seven silent JOBS — reconcile-sent (first at 6:45, BEFORE the 7:00 morning brief, Bug #98-v3's load-bearing ordering), session-sweep, cleanup, weekly-insights, deal-signals (LB1 — Sunday, after insights), identity-reconcile (PID1 — Sunday, after deal-signals), monthly-report — dispatched per fire by `shared/scripts/maintenance_dispatcher.py` (`due_jobs()` decides in code from receipts; the prompt never judges due-ness). One taskId means ONE Run Now grant ever: a future silent job lands inside the already-authorized task instead of creating a new fleet-wide permission gap per release (`task_watchdog`'s `never_authorized` class).

**Supersede step (MAINT1, D5 — data-driven):** after registering each registry task, read `SUPERSEDED_BY[task_id]` from `schedule_config.py`; every listed taskId still registered+enabled is disabled via `update_scheduled_task(enabled: false)`. Idempotent, never deletes (no delete API exists — disable is the only removal). This is the same disable-don't-delete pattern as the Phase 1 legacy migration table; the map is data so the bridge's Phase 4.7 loop applies the identical migration with zero prose duplication.

Compose every silent task's registration parameters in one pass:

```bash
cd "$PLUGIN_ROOT" && WORKSPACE_BASENAME="$WORKSPACE_BASENAME" python3 -c "
import sys, os, json
sys.path.insert(0, 'shared/scripts')
from schedule_config import SILENT_TASKS, DEFAULT_SCHEDULES, compose_silent_task_prompt
basename = os.environ['WORKSPACE_BASENAME'].strip()
out = {}
for task_id, spec in SILENT_TASKS.items():
    out[task_id] = {
        'prompt': compose_silent_task_prompt(task_id, basename),  # raises on bad basename / unsubstituted placeholder
        'cron': DEFAULT_SCHEDULES[task_id]['cron'],               # cron derives from DEFAULT_SCHEDULES — never duplicated in the registry
        'description': spec['description'],
        'notifyOnCompletion': spec['notify'],
        'reason': spec['reason'],                                  # customer-facing one-liner for the Phase 5 ritual
    }
print(json.dumps({tid: {'chars': len(v['prompt']), 'cron': v['cron']} for tid, v in out.items()}))
"
```

Then, for each `task_id` in the composed set: register via `create_scheduled_task(taskId=task_id, prompt=<composed prompt>, cronExpression=<composed cron — from load_schedule_config() when the workspace has an override, else the composed default>, description=<composed description>, notifyOnCompletion=<composed flag>)`. **Idempotent:** if the task already exists with a matching prompt, skip; if it differs, `update_scheduled_task(taskId, prompt=...)` preserving cron/enabled (custom-cron preservation applies to silent tasks exactly as it does to the chats — never re-anchor a registered task's cron from here). Surface one install-summary line per task actually registered this run, e.g. `Registered the weekly cleanup (silent Sunday maintenance + brain self-heal)`.

The `maintenance` task IS in `FIRST_INSTALL_TASK_IDS`, so it registers on fresh installs AND re-runs. Because the prompt asks the dispatcher what's due and then fires the due skills (all read from the installed plugin at fire time), plugin upgrades propagate automatically — no re-registration needed when a silent skill's logic changes, and a NEW silent job added to `maintenance_dispatcher.MAINTENANCE_JOBS` ships with zero registration changes at all.

### Cowork bug awareness — `update_scheduled_task` and #40835

[anthropic/claude-code#40835](https://github.com/anthropics/claude-code/issues/40835) (open as of 2026-05-06): creating or modifying a scheduled task may disable MCP connectors in OTHER existing scheduled tasks. The issue body says "creation/modification" so `update_scheduled_task` is plausibly affected too. There is no per-task MCP-status field exposed in `list_scheduled_tasks` to verify after-the-fact.

**Mitigation:** after any registration / update batch, surface this exact line in the install summary so the customer knows to manually re-prime each task's connector cache:

> *"One quick thing: setting up new scheduled tasks can temporarily switch off access to your email, calendar, and other tools in your other scheduled chats. Open each scheduled chat in Cowork once and confirm any permission prompts. After that, they'll run on schedule with full access."*

If an operator is present for the first-time setup (e.g. immediately after the onboarding call), you can defer this re-prime line to the operator-delivered hand-off rather than surfacing it inline. When the customer is setting schedules up on their own, surface it normally.

If much-older v2.8.x tasks present (`cr-refresh-*`, `cr-daily-morning-pack`, `cr-workflow-commitment-chase-drafts`, `cr-workflow-weekly-audit`), DISABLE those too. Same for any `cr-pulse` or `cr-dont-forget` registrations encountered — both are pre-v2.14.27 state; canonical taskId is bare `pulse` per the migration table above.

## Phase 2 — Load schedule config (v2.14.10+)

Read the per-workspace schedule configuration via the `schedule_config` helper. The helper merges defaults with any overrides stored in entities.json `workspace.schedule_config`:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from schedule_config import load_schedule_config
WORKSPACE = '<absolute path to user workspace>'
config = load_schedule_config(f'{WORKSPACE}/_hq/data/entities.json')
print(json.dumps(config))
"
```

Returned shape: `{taskId: {cron, label, enabled}, ...}` for every default task. Use these config values when registering each schedule in Phase 3 — DO NOT hardcode cron expressions or descriptions in this skill anymore. The hardcoded examples in Phase 3 below are FALLBACKS; the actual values come from the config helper.

**Why config-driven:** different CEOs work different rhythms. A service-business owner may want Inbox at 8 AM (after standups); a fund manager may want it at 6 AM (early Pacific). The helper lets each workspace customize without forking the skill. See `change-schedule` skill (v2.14.10+) for the user-facing customization flow.

**First install behavior:** entities.json typically has no `workspace.schedule_config` field on first install — the helper returns built-in defaults silently. After install, the operator or the customer says `change my schedule` to tune.

**Disabled tasks:** if `config[taskId].enabled` is `false`, skip the registration entirely (or call `update_scheduled_task` with `enabled: false` if currently registered). Disabled tasks remain in the user's Scheduled section as historical reference but won't fire.

**Defaults (built-in fallbacks if config absent):**
- **Time zone:** detected from entities.json primary user (`person.time_zone` field if set), else system time zone
- **Per-chat times:** see `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES` (Morning Brief 7 AM / Upcoming 6:30 AM / Inbox 7:15 AM / Commitments 8:30 AM / Pulse 9 AM / Past Meetings 5 PM, weekdays; Friday Wrap 1 PM Fridays)

## Phase 3 — Register or refresh the 7 orchestrators (6 daily widgets + 1 weekly recap)

**Later-add fence (MAINT1 / D7 — BINDING, before anything registers):** the registration set is NEVER expanded by trigger phrasing; "set up ALL command room scheduled tasks" registers the same first-install set, and every task in `later_add_task_ids()` is PROPOSED (one line each, register only on explicit per-task yes). The later-add chats (`commitments`, `pulse`, `relationship-moves`, `commitment-triage`, `staff-meeting`, `balance`, `pipeline-digest`) are deliberately not first-install because they need accumulated workspace signal to fire well — an "all"-shaped request is enthusiasm, not consent to register tasks that will fire badly on day 1 (the observed 2026-07 field failure: a fresh client said "set up all command room scheduled tasks" and got all four later-add chats registered day 1, against the substrate gating). **`balance` carries one EXTRA proposal gate (BAL1):** propose it ONLY when `entities.json` `workspace.personal_calendars` is declared and non-empty — it is opt-in AND calendar-gated; with no personal calendar its proposal line is replaced by nothing (the feature turns on later via "connect a personal calendar to turn on Balance" → `add balance`). **`pipeline-digest` carries the same shape of EXTRA gate (PIPE1 Part 2):** propose it ONLY when the workspace has ≥1 OPEN tracked deal (`deal_state.list_open_deals` non-empty — the `schedule_proposals` qualifier enforces it in code); a digest over an empty pipeline is noise, and the pipeline-tracker skill's own `digest.enabled` preference never registers anything by itself.

**First-install gate (M1 / 2026-05-23+):** before iterating ORCHESTRATOR_MAP, decide which subset of taskIds gets registered:

```python
from schedule_config import FIRST_INSTALL_TASK_IDS

if FIRST_INSTALL:
    # Fresh workspace per Phase 0.C detection. Register ONLY the 5 M1 first-install tasks.
    # The remaining later-adds (waiting-on / my-plate) arrive via operator-driven
    # follow-up sessions — the two CTS1 commitment surfaces land once the customer
    # has been logging meetings for a couple of weeks (same posture the retired
    # `commitments` chat had).
    tasks_to_register = {
        tid: fname
        for tid, fname in ORCHESTRATOR_MAP.items()
        if tid in FIRST_INSTALL_TASK_IDS
    }
else:
    # Existing workspace. Re-run / refresh — preserve whatever the customer already has
    # registered (do NOT auto-disable waiting-on/my-plate if they're already
    # running; a still-registered `commitments` migrates per the Phase 1 table).
    # A RETIRED task (schedule_config.RETIRED_TASKS) is likewise never auto-disabled
    # here — retirement is PROPOSED by the update bridge and executed by the
    # customer's own `pause`, never by this skill (LIFECYCLE1 §4). It is also never
    # ADDED: filter it out of target_set so a re-run cannot resurrect it.
    # AND make sure the M1 first-install set lands so pre-M1 customers get inbox added
    # on their next re-run. The union behavior is intentional: we add new defaults but
    # never silently remove what the customer has.
    existing_registered = set(load_registered_taskIds())  # from workspace_config.json
    target_set = existing_registered | FIRST_INSTALL_TASK_IDS  # ensure the M1 set lands
    tasks_to_register = {
        tid: fname for tid, fname in ORCHESTRATOR_MAP.items() if tid in target_set
    }
```

The migration semantics:
- Fresh install (M1) → 5 tasks total (`morning-brief`, `upcoming-meetings`, `past-meetings`, `inbox`, `friday-wrap`).
- Existing pre-M1 customer who re-runs the skill → gets their existing tasks refreshed PLUS `inbox` added (because it's now in the M1 first-install set). They never lose tasks they had.
- Customer says `add waiting on` / `add my plate` in Phase 6 management flow → those taskIds get registered individually (`add commitments` maps to registering BOTH `waiting-on` and `my-plate` — the split successors).
- Customer says `add pulse` → **refuse**, with `schedule_config.retirement_line("pulse")` verbatim. A retired task is never registered by any path, including an explicit ask (LIFECYCLE1).

Per Phase 1's `ORCHESTRATOR_MAP`, each taskId in `tasks_to_register` goes through one of three paths based on detection:

- **Not yet registered** → call `mcp__scheduled-tasks__create_scheduled_task` with `prompt=body` from Phase 1's read step. Full registration.
- **Already registered with stale prompt (hash mismatch)** → call `mcp__scheduled-tasks__update_scheduled_task(taskId, prompt=body)`. Refresh in place. Cron, description, enabled, notify all preserved.
- **Already registered with current prompt (hash match)** → skip. No-op idempotent.

**Custom-cron preservation (MANDATORY on every re-run).** When a task is already registered, NEVER pass `cronExpression` to `update_scheduled_task` — pass `prompt` only, so the registered cron is preserved verbatim. An operator (or the user) may have moved a task via `change-schedule`; that override is stored in entities.json `workspace.schedule_config` AND already reflected in the live task. Re-applying `DEFAULT_SCHEDULES` cron on a refresh would silently stomp it back to the shipped default — the exact "my 6 AM brief jumped back to 7 AM after an update" complaint. `cronExpression` is set ONLY on the create path (a task not yet registered), and even there it comes from `load_schedule_config()` (which merges the operator's entities.json overrides), not from raw `DEFAULT_SCHEDULES`. If you genuinely need to re-anchor a cron, that is `change-schedule`'s job, not registration's.

**Pass the full `body` string from Phase 1 as the `prompt` parameter. NEVER paraphrase, summarize, or extract a "mission section" instead.** The orchestrator IS the work — there's no separate runner code; the Claude session executes everything from the prompt. Loss of the v2.13.0 OUTPUT CONTRACT preamble (which lives in the first ~50 lines of every chat-emitting orchestrator) means the fire bypasses every validator + the renderer + the STOP CONTRACT enforcement chain. That's the v2.14.20 regression this v2.14.21 spec exists to prevent.

**Per-task registration template (v2.14.10+ config-driven):**

For each taskId below, pull `cron` + `label` from the Phase 2 config map. Build the `description` parameter by combining the display name + the config's `label` field. Example:

```python
config = load_schedule_config(...)  # from Phase 2
task_id = "inbox"
spec = config[task_id]               # {"cron": "15 7 * * 1-5", "label": "7:15 AM weekdays", "enabled": True}
display = task_display_name(task_id) # "Inbox"
description = f"{display} - Command Room"  # v2.14.25+ canonical format
cron_expression = spec["cron"]
```

If `spec["enabled"]` is `False`, skip registration for this task entirely (or update its enabled flag if already registered).

### Schedule 0 — Morning Brief (onboarding-v2 / 2026-05-17+, NEW)

- `taskId: "morning-brief"` (NEW — first-install default. Registered fresh on every new workspace.)
- `description`: **`"Morning Brief - Command Room"`** (v2.14.25+ canonical display name format)
- `cronExpression`: from config (default `"0 7 * * 1-5"`, 7 AM weekdays). v3.12.0 shifted the `inbox` default to 7:15 AM so there's no slot collision out of the box.
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template (see Phase 1.B); orchestrator body lives at `references/orchestrator-morning-brief.md` and is read fresh by the bootloader at fire time. The orchestrator wraps the existing `morning-briefing` skill — keeps the scheduled-fire output and the on-demand `morning briefing` / `brief me` / `what do I need to know today` output convergent (one source of truth for the morning-briefing format).

### Schedule 1 — Upcoming Meetings

- `taskId: "upcoming-meetings"` (v2.14.27+ — bare taskId so Cowork's sidebar title renders cleanly as "Upcoming meetings"; prior cr-upcoming-meetings → migration disabled)
- `description`: **`"Upcoming Meetings - Command Room"`** (v2.14.25+ canonical display name)
- `cronExpression`: from config (default `"30 6 * * 1-5"`)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template (see Phase 1.B); orchestrator body lives at `references/orchestrator-upcoming-meetings.md` and is read fresh by the bootloader at fire time.

### Schedule 2 — Inbox

- `taskId: "inbox"` (v2.14.27+ — bare taskId; prior cr-inbox → migration disabled)
- `description`: **`"Inbox - Command Room"`** (v2.14.25+ canonical display name)
- `cronExpression`: from config (default `"15 7 * * 1-5"`, 7:15 AM — v3.12.0 shifted off the 7:00 morning-brief slot)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body at `references/orchestrator-inbox.md`.

### Schedule 3 — Waiting On + My Plate (CTS1 — the split successors of "Commitments")

- `taskId: "waiting-on"` (CTS1 §10.3, fresh taskId per the v2.14.27 pattern; prior `commitments` → migration disabled per the Phase 1 table)
- `description`: **`"Waiting On - Command Room"`** (v2.14.25+ canonical display name format)
- `cronExpression`: from config (default `"30 8 * * 1-5"` — inherits the old commitments slot; a customer's custom cron override on `commitments` carries over to this task at migration)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body at `references/orchestrator-commitments.md` (filename kept for events.jsonl source_skill back-compat — events keep `source_skill='commitments'`, same pattern as pulse).

- `taskId: "my-plate"` (CTS1 §10.3 — the second surface; registers alongside waiting-on)
- `description`: **`"My Plate - Command Room"`**
- `cronExpression`: from config (default `"45 8 * * 1-5"` — 15 minutes after waiting-on so it reads the just-reconciled substrate)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body at `references/orchestrator-my-plate.md`.

### Schedule 4 — Pulse — RETIRED (SPEC LIFECYCLE1, 2026-08-02)

**Do not register this task. There is no registration spec here any more, and that absence is the ruling.** `pulse` is in `schedule_config.RETIRED_TASKS` and out of `DEFAULT_SCHEDULES`; the `ORCHESTRATOR_MAP` row survives only so a pre-retirement registration still resolves its bootloader to `references/orchestrator-dont-forget.md`, which is now a retirement stub. Refreshing that prompt on a still-registered task is CORRECT — it is what makes the next fire explain itself instead of replaying the old chat. The historical registration facts follow so an auditor can recognise an existing registration. They are a description of what IS, never an instruction to create one:

- `taskId: "pulse"` (v2.14.27+ — bare taskId aligned with display name; prior cr-dont-forget → migration disabled. Orchestrator filename stays as `orchestrator-dont-forget.md` for events.jsonl source_skill back-compat — historical events with source_skill='cr-dont-forget' remain valid as append-only history; new events post-v2.14.27 use source_skill='pulse'.)
- `description`: **`"Pulse - Command Room"`** (v2.14.25+ canonical display name)
- `cronExpression`: from config (default `"0 9 * * 1-5"`)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body at `references/orchestrator-dont-forget.md`.

### Schedule 5 — Past Meetings

- `taskId: "past-meetings"` (v2.14.27+ — bare taskId; prior cr-past-meetings → migration disabled)
- `description`: **`"Past Meetings - Command Room"`** (v2.14.25+ canonical display name)
- `cronExpression`: from config (default `"0 17 * * 1-5"`)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body at `references/orchestrator-past-meetings.md`.

### Schedule 6 — Friday Wrap (v3.11.0+, NEW)

- `taskId: "friday-wrap"` (NEW — first-install default. Registered fresh on every new workspace post-v3.11.0.)
- `description`: **`"Friday Wrap - Command Room"`** (v2.14.25+ canonical display name format)
- `cronExpression`: from config (default `"0 13 * * 5"`, 1 PM Fridays — Phase 3/R4 moved the default off the routinely-slept-through 4 PM slot for NEW installs; existing registrations keep their live cron per the custom-cron preservation rule). First weekly-rhythm scheduled task; all prior tasks are daily.
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body lives at `references/orchestrator-friday-wrap.md` and is read fresh by the bootloader at fire time. The orchestrator wraps the existing `weekly-recap` skill — keeps the scheduled-fire output and the on-demand `weekly recap` / `recap last week` output convergent (one source of truth for the recap format and `.docx` save path).

### v2.14.25+ — Schedule 7 (Workspace Map refresh) DROPPED

The v2.14.11+ daily auto-refresh scheduled task `cr-refresh-workspace-map` is REMOVED from the active task set as of v2.14.25. Per M's call: the daily auto-rebuild of the Workspace Map sidebar artifact wasn't worth the operational complexity (one more task to register, one more cron to fire, one more #40835 risk surface).

**What remains:** the Workspace Map artifact itself stays fully functional. Customers install via `enable workspace map` / `install workspace map`; the artifact's manual `↻ Refresh` button still triggers an ad-hoc rebuild on click; on-demand commands like `rebuild workspace map` still work. Only the daily auto-refresh cron at 4 PM weekdays is gone.

**Migration for existing customers (anyone with the task already registered):** Phase 1's legacy-taskId migration list (above) now includes `cr-refresh-workspace-map`. On next `set up command room schedules` run, the task is DISABLED via `update_scheduled_task(enabled: false)` and surfaced in the install summary as: *"Removed the daily Workspace Map auto-refresh — the `↻ Refresh` button on your Workspace Map still works."*

For each created schedule, log a `schedule_created` event (OMIT `seq`/`ts` — the append gate auto-stamps both inside the writer lock, `ts` in UTC; a hand-typed "now" was the F-15 naive-local-clock bug class, v4.5.2 R4.)
```jsonl
{"type":"schedule_created","data":{"taskId":"<id>","cron":"<expr>","label":"<description>"}}
```
**MANDATORY on EVERY registration path (FS-07).** This write fires whenever a task is newly registered — the Phase 3 first-install loop AND the Phase 6 `add` flow (`add staff meeting`, `add commitments`, …). A task that lands in Cowork's scheduler with no `schedule_created` event has no substrate record of its registration — that is the FS-07 gap (the live `add staff meeting` created the task + updated `workspace_config.json` but wrote nothing). One `schedule_created` per taskId actually created this run, never for a task that was already registered (idempotent skip).

## Phase 3.5 — Post-registration verification (v2.14.24+ — bootloader pattern)

After every `create_scheduled_task` / `update_scheduled_task` call in Phase 3, **read back what's now registered and verify it's the canonical bootloader (not an agent-improvised stub).** This is the hard gate that catches v2.14.20-style improvisation regressions, adapted for the bootloader pattern.

What's verified:
1. Each registered taskId's prompt CONTAINS the canonical bootloader markers (`# Scheduled task bootloader`, `Resolve the plugin path`, `Read the orchestrator and execute it verbatim`).
2. Each registered taskId's prompt CONTAINS the correct task name (`<TASK_ID>` was substituted with the actual taskId, not left as a literal placeholder).
3. Each registered taskId's prompt CONTAINS the correct orchestrator filename (`<ORCHESTRATOR_FILENAME>` was substituted with the file from `ORCHESTRATOR_MAP`).
4. Each registered taskId's prompt does NOT start with `---` frontmatter (Cowork prepends its own; user-supplied frontmatter creates a doubling bug).
5. Each registered taskId's prompt length is within **0.9×–1.5× of the composed bootloader for that task** (`len(bootloaders[task_id])` from Phase 1.B — computed at RUN TIME from the real template, never a hardcoded range). Under 0.9× means a stub got registered; over 1.5× means the full orchestrator body (or other bloat) got registered. (Phase 3 / P0.2: hardcoded ranges drifted and failed every healthy install — see references/HISTORY.md § Phase 3 / P0.2. Computing bounds from the template kills the drift class; the composition test in tests/run_bootloader_size_gate_test.py keeps gate and template from ever drifting again.)

```python
# Pseudocode — translate to actual MCP calls in your invocation
registered = mcp__scheduled_tasks__list_scheduled_tasks()
registered_by_id = {t["taskId"]: t for t in registered}

REQUIRED_MARKERS = [
    "# Scheduled task bootloader",
    "Resolve the plugin path",
    "Read the orchestrator and execute it verbatim",
    "Anti-improvisation contract",
]

failures = []
# Iterate over what we INTENDED to register this run (Phase 3's tasks_to_register subset),
# not the full ORCHESTRATOR_MAP. On first-install runs the deferred tasks
# (waiting-on/my-plate and the other later-adds) are intentionally NOT
# registered — they're not failures. Neither is a RETIRED task, ever.
for task_id, fname in tasks_to_register.items():
    if task_id not in registered_by_id:
        failures.append(f"{task_id}: not registered")
        continue
    actual_prompt = registered_by_id[task_id]["prompt"]
    # Frontmatter-doubling check
    if actual_prompt.lstrip().startswith("---"):
        failures.append(f"{task_id}: registered prompt starts with frontmatter (Cowork doubling bug); re-register without leading ---")
        continue
    # Marker checks
    for marker in REQUIRED_MARKERS:
        if marker not in actual_prompt:
            failures.append(f"{task_id}: registered prompt missing required bootloader marker {marker!r}")
            break
    else:
        # All markers present — verify task-specific substitutions
        if f"`{task_id}`" not in actual_prompt:
            failures.append(f"{task_id}: registered prompt is missing the task name {task_id!r} in expected location (substitution may have failed)")
            continue
        if fname not in actual_prompt:
            failures.append(f"{task_id}: registered prompt is missing the orchestrator filename {fname!r} (substitution may have failed)")
            continue
        # v2.14.26+ — workspace basename substitution check
        # The bootloader's Step 1 must contain the customer-confirmed basename. If it still
        # has the literal "<WORKSPACE_BASENAME>" placeholder, Phase 0 / Phase 1.B failed and
        # the bootloader will fall back to discovery on every fire (works but suboptimal).
        if "<WORKSPACE_BASENAME>" in actual_prompt:
            failures.append(f"{task_id}: registered prompt has unsubstituted <WORKSPACE_BASENAME> placeholder — Phase 0 customer confirmation may not have run, or Phase 1.B substitution failed")
            continue
        # Verify the basename string we intended to bake is actually present in the path-resolution context.
        expected_workspace_path = f'$SESSION_DIR/mnt/{workspace_basename}'  # workspace_basename from Phase 0
        if expected_workspace_path not in actual_prompt:
            failures.append(f"{task_id}: registered prompt is missing the expected workspace path {expected_workspace_path!r} (basename substitution may have written to wrong location)")
            continue
        # Size sanity check (Phase 3 / P0.2) — bounds computed from THIS RUN's
        # composed bootloader, never a hardcoded range (hardcoded ranges drift
        # the moment the template changes — the pre-Phase-3 gate failed every
        # healthy install for exactly that reason).
        expected = len(bootloaders[task_id])   # from Phase 1.B composition
        if len(actual_prompt) > expected * 1.5:
            failures.append(f"{task_id}: registered prompt is {len(actual_prompt)} chars vs ~{expected} composed — too large for a bootloader. Did the full orchestrator body get registered by mistake?")
            continue
        if len(actual_prompt) < expected * 0.9:
            failures.append(f"{task_id}: registered prompt is {len(actual_prompt)} chars vs ~{expected} composed — too small. Stub-improvisation regression?")
            continue
```

**Surface failures in plain English.** If any task fails verification:

> *"Couldn't finish setting up: [list]. Say `set up command room schedules` again to retry. If it keeps failing, reinstall Command Room the same way it was first installed — or ask whoever set it up for you."*

**Display name verification (v2.14.25+):** also verify each registered task's `description` field matches the canonical "X - Command Room" format. Specifically:
- `morning-brief` → `"Morning Brief - Command Room"`
- `upcoming-meetings` → `"Upcoming Meetings - Command Room"`
- `inbox` → `"Inbox - Command Room"`
- `waiting-on` → `"Waiting On - Command Room"` (CTS1; the retired `commitments` task keeps whatever description it had — it's disabled, never renamed)
- `my-plate` → `"My Plate - Command Room"` (CTS1)
- `pulse` → `"Pulse - Command Room"` (RETIRED — the description is for renders of a task already registered; never create one)
- `past-meetings` → `"Past Meetings - Command Room"`
- `friday-wrap` → `"Friday Wrap - Command Room"` (NEW v3.11.0)

If the description doesn't match, call `update_scheduled_task(taskId, description=<canonical>)` to fix in place. Display-name drift is a regression class on its own.

**Why this gate exists, adapted for v2.14.24.** The agent-improvisation risk doesn't go away when the pinned content becomes a bootloader — a stub bootloader could still be improvised if the registration step is loose (history of the stub class in references/HISTORY.md § v2.14.20). So Phase 3.5 verifies the registered prompt has the canonical markers + correct substitutions + frontmatter-clean + reasonable size; v2.14.25 adds display-name verification. Catches the same class of bug, scoped to the new pattern.

## Phase 4 — Onboarding integration: register the historical-backfill chunks

This phase fires ONLY when invoked by `command-room-onboarding` (the onboarding skill explicitly passes `--with-backfill` or sets a flag). When fired by direct user trigger, this phase is skipped — backfill is an onboarding concern.

The historical backfill walks the user's last 12 months of email / calendar / files / meetings at **metadata-only** level (no bodies, transcripts, or file content). It runs as a series of one-shot scheduled tasks, chunked to keep each fire's context budget under ~30K tokens.

### Step 1: detect user volume tier

Read the Phase 1 connector counts captured by onboarding (last-30d email count + calendar density + Granola transcript count). Map to a tier:

| Volume signal | Tier | Chunk strategy |
|---|---|---|
| <5,000 emails/year-projected, <500 calendar events | **light** | 3-month chunks × 4 fires, 1 hour apart |
| 5,000-30,000/year-projected | **medium** | 1-month chunks × 12 fires, 1 hour apart |
| >30,000/year-projected | **heavy** | 2-week chunks × 26 fires, 30 min apart |

If onboarding can't pass volume signals (e.g. running this skill standalone), default to **medium**.

### Step 2: register the chunks

For each chunk N (1-based), compute `fireAt = now + N × interval`. Register one-shot scheduled task:

- `taskId: "cr-historical-backfill-N"`
- `description: "Historical backfill chunk N of M — pulls metadata for [chunk window]"`
- `recurrence: "once"`
- `fireAt: "<ISO>"`
- `notifyOnCompletion: false` (don't spam — this runs in the background)
- `prompt`: see `references/orchestrator-historical-backfill.md` *(NOTE: this file should be created in v2.10.2 if not present yet — defer to a future patch if absent at install time and surface a plain-English note: "I couldn't set up the historical catch-up just now — your history will fill in gradually through your daily chats instead.")*

The prompt receives the chunk window (start/end dates) as part of the orchestrator's input. Each chunk's session:
- Fetches metadata for the window from every connector
- Writes events.jsonl in batched appends (no body content, just metadata)
- Creates provisional person + project records for clusters
- Updates the resume marker (`_hq/data/.backfill_cursor`)
- Exits cleanly

### Step 3: log the schedule

For each chunk (OMIT `seq`/`ts` — the append gate auto-stamps both inside the writer lock, `ts` in UTC; a hand-typed "now" was the F-15 naive-local-clock bug class, v4.5.2 R4.)
```jsonl
{"type":"backfill_chunk_scheduled","data":{"chunk_n":N,"of":M,"fireAt":"<ISO>","window_start":"<date>","window_end":"<date>","tier":"<light|medium|heavy>"}}
```

### Step 4: surface to user as part of onboarding close-out

The onboarding skill (NOT this skill) handles the user-facing close-out. This skill just registers the chunks silently and returns the count.

## Phase 5 — Surface install ritual + confirmation (W2 — authorize EVERYTHING, render from the registration set)

The summary block branches on `FIRST_INSTALL` (set in Phase 0.C).

**Render the ritual list from what THIS RUN actually registered — never from hardcoded copy.** The grant count and the task list come from the union of Phase 3's `tasks_to_register` + the Step 1.D silent-task set, with display names via `task_display_name()` and fire labels via `load_schedule_config()`. Hardcoded counts drift the moment the default set changes — the silent-task authorization ghost class is what this ritual exists to kill (see references/HISTORY.md § W2).

**The silent tasks are IN the ritual.** Every newly registered taskId — including the silent ones — appears in the Run Now list with the one-line reason it exists (from `SILENT_TASKS[task_id]["reason"]`). A silent task that never clears Cowork's one-time permission gate never fires, and nothing tells the customer, so the install ritual is the one moment to get all of them authorized.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "Repaired v2.14.20–v2.14.26 registration: disabled legacy pulse-orchestrator task and registered the canonical bare-name `pulse`."
- GOOD: "Fixed an older setup issue — your Pulse chat has been re-registered fresh."

### If `FIRST_INSTALL = True` (M1 default)

Shape (counts + names + times rendered from the actual registration set — the 6 tasks below are the current default set, shown as an example):

```
Command Room schedules registered:

Your daily and weekly chats:
✓ Morning Brief        (7 AM weekdays — runs before your workday)
✓ Upcoming Meetings    (6:30 AM weekdays — preps the day ahead)
✓ Inbox                (7:15 AM weekdays — clears your inbox before the day starts)
✓ Past Meetings        (5 PM weekdays — processes the day's calls)
✓ Friday Wrap          (1 PM Fridays — wraps your week into a recap)

Working quietly in the background (no chat output unless something needs you):
✓ Maintenance          — handles all the background upkeep in one place: closing
                         commitments you finished by email, catching unlogged
                         decisions from your chats, the weekly tidy and insight
                         refresh, and the monthly report

ONE-TIME INSTALL RITUAL — required by Cowork:

[N] permission grants needed — one per task above, INCLUDING the
background ones. Open Cowork's Scheduled section in the sidebar. Every
entry with a yellow dot is waiting on you: tap "Run Now" on each once
to authorize it. The background tasks won't post anything — the Run Now
is just the permission grant. Skip one and it silently never runs.

ONE MORE THING — keep the computer awake:

These tasks run on your computer, so a closed lid or sleep mode when a
task is due means it waits until you're back. If you want the morning
chats ready before you sit down, set your computer to stay awake (or
just plugged in overnight): System Settings → Displays/Power → prevent
sleeping when plugged in. If a task ever looks like it "stopped
working," a sleeping computer is the most common reason — not an error.

Your Commitments chats will be added in a follow-up session once
you've been logging meetings for a couple of weeks. They work best
once there's some history for them to draw on.

To manage anytime: `list my schedules`, `pause [task name]`, or
`change my schedule` to move any of the times.
```

### If `FIRST_INSTALL = False` (existing workspace — refresh / add flow)

**Substrate write on add (FS-07 — MANDATORY):** for every task this run actually registered (the `add <task>` flow's new taskId), log the `schedule_created` event exactly as the Phase 3 loop does (shape + OMIT-seq/ts rule above). The add flow registers with Cowork AND writes the substrate record — skipping the event (the live `add staff meeting` gap) leaves the task with no registration trace for the watchdog / receipts. Idempotent: no event for a task that was already registered.

Same rendering rule: list what exists + what THIS RUN added (names + times from `load_schedule_config()`; live cron wins for already-registered tasks). Keep the migration notes below when they apply:

```
Command Room schedules registered:

[per-task lines rendered from the config — display name + label, silent tasks marked "(background)"]

[If Morning Brief was just added by this re-run, surface:]
Morning Brief is new. If its time collides with another chat's slot,
say `change my schedule` and I'll move one of them.

[If Friday Wrap was just added by this re-run, surface:]
Friday Wrap is new — it runs Fridays and wraps your week into a recap
you can read here or forward. Don't forget to authorize it the first
time it appears.

[If any SILENT_TASKS entry was just added by this re-run (Step 1.D / Phase 5.9), surface each with its reason:]
Also added in the background: [Display Name] — [reason]. It needs one
Run Now in the Scheduled section to authorize, then it runs on its own.

[If migrating from v2.9-v2.10.1, add:]
Migrated from prior version:
  • Meetings Today → Upcoming Meetings
  • Inbox Pulse → Inbox
  • Commitments You Owe + Commitments Owed To You → Commitments (merged)
  • Cracks Watch → Pulse
  • Meetings Processed → Past Meetings
The old entries are switched off — your scheduled chats now live under the new names.

[If migrating from a v2.14.20 broken state where cr-pulse or cr-dont-forget was registered, add:]
Fixed an older setup issue — your Pulse chat has been re-registered fresh.

ONE-TIME INSTALL RITUAL — required by Cowork (only for tasks added this run):

Each newly-registered task needs a manual permission grant the first
time it runs — including the background ones, which won't post anything
but still need the one-time Run Now. Open Cowork's Scheduled section in the
sidebar. Click "Run Now" on any task with a yellow dot to authorize
tool access. (Tasks you've already authorized stay authorized.)

To manage anytime: `list my schedules`, `pause [task name]`, `change my schedule`.
```

## Phase 5.9 — Silent-task registration assertion (v3.18.2+, Bug #82 — UNCONDITIONAL, runs before any Phase 6 early-exit; Phase 3 / SPEC-2.3: one loop over the SILENT_TASKS registry)

**This check is mandatory on EVERY invocation, including the re-run / "already configured" path.** Before surfacing the Phase 6 management prompt (or taking any "all current — nothing to do" early-exit), you MUST verify every task in the `SILENT_TASKS` registry (`shared/scripts/schedule_config.py` — currently one task, `maintenance`) is registered, AND that every taskId its `SUPERSEDED_BY` entry lists is disabled (the MAINT1 migration is part of this gate: an existing install re-running setup gets the five old silent tasks switched off and `maintenance` registered here, even on the early-exit path).

**Why this is its own gate.** The idempotency checks above iterate `ORCHESTRATOR_MAP` (the 7 chats). The silent tasks are intentionally NOT in that map — they are not chat-orchestrators and register separately via **Step 1.D**. So the "are all chats registered?" check is structurally blind to them: on an existing workspace (all 7 chats present), the skill reports "all current" and routes straight to Phase 6, and **Step 1.D is never reached** — exactly the v3.18.1 failure (Bug #82 — see references/HISTORY.md § Bug #82 silent-task registration miss). The silent tasks ARE in `FIRST_INSTALL_TASK_IDS`, so Phase 3's `target_set = existing | FIRST_INSTALL_TASK_IDS` already contains them — this gate makes the assertion explicit and unconditional so no branch can skip it. Looping over the registry (instead of one hand-written bullet per task, the pre-Phase-3 shape) means a future silent task cannot be forgotten here: it's covered the moment it lands in `SILENT_TASKS`.

**Do this:**

1. Call `mcp__scheduled-tasks__list_scheduled_tasks`.
2. For EVERY `task_id` in `SILENT_TASKS`: if no task with that taskId is present → **run Step 1.D now for that task** (idempotent — if it somehow exists with a stale prompt, Step 1.D updates in place), including Step 1.D's supersede step (disable every still-enabled taskId in `SUPERSEDED_BY[task_id]`). This is the generalization of the Friday-Wrap generic-add path in `command-room-update-bridge` Phase 4.7.
3. Surface one install-summary line per task this gate actually registered (from the registry's `description`/`reason` — don't announce a no-op).
4. Only after the loop completes may you continue to Phase 6.

```python
# Pseudocode — translate to actual MCP calls.
from schedule_config import SILENT_TASKS
registered_ids = {t["taskId"] for t in mcp__scheduled_tasks__list_scheduled_tasks()}
for task_id in SILENT_TASKS:
    if task_id not in registered_ids:
        run_step_1D(task_id)   # register from the registry — NEVER skipped by the Phase 6 early-exit
        summary_lines.append(f"Registered {SILENT_TASKS[task_id]['description']}")
```

**Authorization follow-through (W2, Phase 3 reliability).** Registration is not the finish line — a silent task that never clears Cowork's one-time Run Now permission never fires, forever, with no symptom. Two backstops close that:

- The Phase 5 install ritual (above) lists EVERY newly registered taskId **including the silent ones**, each with its one-line reason, so the customer authorizes all of them in one sitting — not just the visible chats.
- The scheduled-task watchdog (`shared/scripts/task_watchdog.py`, surfaced by the morning brief and cleanup's Monday note) checks fired-recency receipts per task. If a registered task has no substrate receipt within 3 weekdays of registration, it reports `never_authorized` and the next visible surface tells the customer exactly which task to open and Run Now. Nothing in this skill needs to poll — the watchdog runs inside surfaces that already fire.

## Phase 6 — Re-run / management

Re-firing this skill detects existing schedules. Surfaces:

> *"Command Room schedules already configured. [N] scheduled chats running. Want to add, change, remove, or reset? (add / change / remove / reset / nothing)"*

(`[N]` is the count of currently-enabled registered taskIds — typically 5 on a fresh M1 install, up to 7 once the Commitments chats have been added.)

- `add` — only useful if a future version adds new chats
- `change` — list existing, ask which + new cron. THIS is the calibration entry path (v2.9.2+ doesn't ask cadence questions at first install; explicit `change` request opens that conversation).
- `remove` — list existing, ask which to disable (no delete API)
- `reset` — disable everything + re-register all v2.10.2 (fresh state, defaults)
- `nothing` — exit silently

### Explicit calibration intent

If the user fires this skill with `customize my command room schedules` / `change my schedule cadence`, route directly into Phase 6 `change` flow without the "already configured" preamble.

## Reference files

The chat-emitting orchestrator prompts live in `references/` (workspace-map refresh was retired in v2.14.25):

- `orchestrator-morning-brief.md` (taskId `morning-brief`; display "Morning Brief"; wraps the `morning-briefing` skill)
- `orchestrator-upcoming-meetings.md` (taskId `upcoming-meetings`)
- `orchestrator-inbox.md` (taskId `inbox`)
- `orchestrator-commitments.md` (taskId `waiting-on`; display "Waiting On" — CTS1: filename kept for backward compat with events.jsonl `source_skill='commitments'` history, same pattern as pulse below)
- `orchestrator-my-plate.md` (taskId `my-plate`; display "My Plate" — CTS1 Surface 2)
- `orchestrator-dont-forget.md` (taskId `pulse` — **RETIRED, LIFECYCLE1**; the file is a retirement stub kept so a pre-retirement registration's bootloader still resolves. Never register it.)
- `orchestrator-past-meetings.md` (taskId `past-meetings`)
- `orchestrator-friday-wrap.md` (taskId `friday-wrap`; display "Friday Wrap"; NEW v3.11.0 — wraps the `weekly-recap` skill; first weekly-rhythm scheduled task)

`orchestrator-refresh-workspace-map.md` exists in the references folder as a historical artifact only; it's not in `ORCHESTRATOR_MAP` and never registered.

**There is no `orchestrator-pulse.md` file**, and since LIFECYCLE1 there is no Pulse chat either. If you find a registered task with `taskId: "cr-pulse"`, `cr-dont-forget`, or the bare `pulse`, do NOT re-register any of them — the class is retired. Disable a legacy `cr-*` variant per the Phase 1 migration table and leave the bare `pulse` alone for the customer's own `pause` (LIFECYCLE1 §4).

Tombstones (back-compat pointers; don't reference directly in new schedules):
- `orchestrator-meetings-today.md` *(does not exist — file was renamed)*
- `orchestrator-inbox-pulse.md` *(does not exist — file was renamed)*
- `orchestrator-commitment-nudge.md` (tombstone pointing at orchestrator-commitments.md)
- `orchestrator-commitment-chase.md` (tombstone pointing at orchestrator-commitments.md)
- `orchestrator-cracks-watch.md` *(does not exist — file was renamed)*
- `orchestrator-meetings-processed.md` *(does not exist — file was renamed)*

Plus shared specs:
- `SHARED_CHAT_OUTPUT_PROTOCOL.md` (in this `references/` folder) — universal chat-output rules (the 10 rules that apply across every orchestrator)
- `shared/EMAIL_DRAFT_PROTOCOL.md` (in the plugin's `shared/` folder, moved out of this skill's `references/` in v3.13.3 to reflect its universal scope — referenced by email-writer / intro-broker / follow-up-ritual / inbox-triage / thread-resurrection as well as the scheduled orchestrators) — email-draft mechanics (lazy creation, Gmail/Outlook MCP defensive handling)
- `PROJECT_MAPPING_RULES.md` (in this `references/` folder) — deterministic 4-rule project resolution + plain-English unrouted heuristic

(Note: `STAGING_CONVENTION.md` and `PROVENANCE_FRONT_MATTER.md` were retired in v3.12.0. Deliverables route through `_hq/meetings/` via `brief_writer.py` per `MD_DELIVERABLE_POLICY.md`; `_hq/staging/` is now a forbidden path per the leak scanner.)

## Forbidden behaviors

- **Don't create schedules without confirmation** in interactive mode.
- **Don't duplicate schedules** — Phase 1 idempotency check is non-negotiable.
- **Don't auto-fire on creation** — `create_scheduled_task` registers; Cowork's first-fire permission ritual is the user's job.
- **Don't write to `_hq/staging/[date]/`** — that path was retired in v3.12.0 and is now an active leak-pattern scan target. Deliverables go through `brief_path.get_brief_path()` to `_hq/meetings/` or the typed deliverable subfolders.
- **Don't bypass the orchestrator reference files.** Each scheduled task's prompt must be the EXACT text from its reference file — tested behavior depends on the prompt being byte-stable.
- **Don't fire historical-backfill chunks outside onboarding.** Phase 4 is gated to onboarding-invoked runs only.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Sets up Cowork scheduled tasks for Command Room — daily + weekly action chats that produce drafts and surface decisions for review. On a fresh-install workspace (M1, 2026-05-23+), registers 5 tasks (`morning-brief`, `past-meetings`, `inbox`, `upcoming-meetings`, `friday-wrap`) — the 5 chats that establish the customer's daily and weekly rhythm. The remaining 2 defaults (`commitments`, `pulse`) get added later via operator-driven follow-up sessions once enough workspace signal exists for them to fire well. On re-runs against an already-configured workspace, the existing Phase 6 (`change` / `add` / `remove` / `reset`) management flow handles task adjustments. Each chat = 1 scheduled task = 1 persistent thread in Cowork's Scheduled section. **Phase 0.5 opens with a substantive vanilla-vs-Command-Room explainer** before any registration happens — customers learn why scheduled tasks loaded with their substrate beat vanilla scheduled tasks before they authorize the 5. Triggers: 'set up command room schedules', 'enable schedules', 'register my scheduled chats', 'verify command room prompts', 'check my command room version', 'which version are my tasks on'. The registration set is NEVER expanded by trigger phrasing — "set up ALL command room scheduled tasks" registers the same first-install set, and later-add tasks are only ever proposed, never auto-registered (MAINT1 / D7 fence). DOES NOT fire on 'configure my schedules' / 'change my schedule' / 'customize my schedules' (change-schedule — cadence customization of already-registered chats; this skill registers them). Also called silently by `command-room-update-bridge` post-install + by `command-room-onboarding` for the historical-backfill registration (onboarding does NOT pass `--with-backfill` on the M1 first-install flow). Idempotent: re-runs surface the current set instead of duplicating.
