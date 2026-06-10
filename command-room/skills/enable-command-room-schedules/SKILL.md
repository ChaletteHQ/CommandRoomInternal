---
name: enable-command-room-schedules
description: "Sets up Cowork scheduled tasks for Command Room — daily + weekly action chats that produce drafts and surface decisions for review. On a fresh-install workspace (M1, 2026-05-23+), registers 5 tasks (`morning-brief`, `past-meetings`, `inbox`, `upcoming-meetings`, `friday-wrap`) — the 5 chats that establish the customer's daily and weekly rhythm. The remaining 2 defaults (`commitments`, `pulse`) get added later via operator-driven follow-up sessions once enough workspace signal exists for them to fire well. On re-runs against an already-configured workspace, the existing Phase 6 (`change` / `add` / `remove` / `reset`) management flow handles task adjustments. Each chat = 1 scheduled task = 1 persistent thread in Cowork's Scheduled section. **Phase 0.5 opens with a substantive vanilla-vs-Command-Room explainer** before any registration happens — customers learn why scheduled tasks loaded with their substrate beat vanilla scheduled tasks before they authorize the 5. Triggers: `set up command room schedules`, `enable schedules`, `configure my schedules`, `verify command room prompts`, `check my command room version`, `which version are my tasks on`. Also called silently by `command-room-update-bridge` post-install + by `command-room-onboarding` for the historical-backfill registration (onboarding does NOT pass `--with-backfill` on the M1 first-install flow). Idempotent: re-runs surface the current set instead of duplicating."
---

## v2.13.0+ — Verify-only mode (preview without firing)

Triggers: `verify command room prompts` / `check my command room version` / `which version are my tasks on`

When fired with one of these phrases, this skill runs in **read-only verification mode** — it inspects the registered prompts WITHOUT updating anything. Output: per-task version + status. Use this BEFORE firing scheduled tasks if you want to confirm the prompts are current.

**Verification flow:**

1. **Read the installed plugin version** from `$PLUGIN_ROOT/.claude-plugin/plugin.json` (resolve `$PLUGIN_ROOT` via the canonical CONTRACT.md Rule 22 preamble: `SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)`) — that's the `plugin_version` to display.
2. Call `mcp__scheduled-tasks__list_scheduled_tasks`. Get all 5 `cr-*` task entries.
3. For each task, read the registered prompt content (from the path field on the task record, or directly from the prompt string).
4. Run contract-version detection on the prompt content:
   - Contains `OUTPUT CONTRACT (v2.13.0+` → contract v2.13.0+ (current)
   - Contains `v2.12.6` and not v2.13.0 marker → contract v2.12.6
   - Contains `v2.12.5` only → contract v2.12.5
   - Contains `v2.12.4` only → contract v2.12.4
   - Older versions → list as such
   - No version marker → unknown / very old
5. **Display BOTH** the installed plugin version AND the contract version in each task's row, so the user sees both pieces of state. Per M's v2.13.2 ask: *"why is it not v2.13.2"* — the prior display only showed contract version, hiding the plugin version.

   Format:

   ```
   Command Room scheduled-task verification:

   Plugin: v3.0.0 installed.

   ✓ upcoming-meetings       contract v2.13.0+  (current)
   ✓ inbox                   contract v2.13.0+  (current)
   ✓ commitments             contract v2.13.0+  (current)
   ✓ pulse                   contract v2.13.0+  (current)
   ✗ past-meetings           contract v2.12.6   (refresh needed)

   4 of 5 on the v2.13.0+ contract. Run `set up command room schedules` to refresh the rest.
   ```

6. If ALL 5 are current, confirm: `All 5 prompts on the v2.13.0+ contract under plugin v2.13.2 — fire any task with confidence.`
7. If ANY are stale, the report ends with the explicit instruction to run the refresh trigger.

**Do NOT update any prompt during verify-only mode.** This is observability, not mutation.

Verification mode is the diagnostic version of the install ritual — gives the user explicit visibility into which prompts are current BEFORE they hit fire-and-find-out.

---

# enable-command-room-schedules (M1, 2026-05-23)

The schedule-setup skill. Configures **7 topic-specific persistent chats** + a one-time **historical backfill** sweep via `mcp__scheduled-tasks__create_scheduled_task`. Each chat = 1 stable taskId = 1 persistent thread in Cowork's Scheduled sidebar section, accumulating turns over time.

**On a fresh-install workspace, only 5 of the 7 fire automatically** — `morning-brief`, `past-meetings`, `inbox`, `upcoming-meetings`, `friday-wrap`. The remaining 2 (`commitments`, `pulse`) get added later via operator-driven follow-up sessions when accumulated workspace signal makes them useful.

## Phase 0.5 — Chat 2 substantive explainer (M1, fresh-install only)

When this skill fires from a fresh-install workspace AND the parent context is `command-room-onboarding` Phase 1a (Chat 2 just opened with `set up command room schedules`), surface the substantive vanilla-vs-Command-Room explainer below BEFORE Phase 0 workspace-discovery runs. This is the Chat 2 education beat from the M1 spec — it gives the customer the "why" behind the 5 they're about to authorize.

Skip this phase if the skill fires from `command-room-update-bridge` post-install (silent registration; no customer in the chat), from a re-run (`FIRST_INSTALL = False` per Phase 0.C detection — customer already knows), or from any explicit calibration trigger (`change my schedule`, etc.).

Read `workspace.brain_name` from the customer's entities.json if available — substitute for `[BrainName]` below. If not yet set (entities.json not seeded yet because onboarding is still mid-flight), default to "Penelope."

**Surface this verbatim (one chat message):**

> *"Scheduled tasks are how [BrainName] reaches out to you — she starts the conversation instead of you having to remember to ask. Each one is a chat that fires on its own cadence and produces output you read like any other conversation. The chat appears in your sidebar under 'Scheduled.'*
>
> *You can set up scheduled tasks in Cowork without Command Room. The reason they're significantly more useful with Command Room is everything [BrainName] reads when she fires one.*
>
> ***A vanilla scheduled task fires cold.*** *It asks the AI to do something with no memory of who you are. Each fire starts from zero — you'd have to re-explain your business, your people, your priorities every time.*
>
> ***A Command Room scheduled task fires with full context loaded.*** *[BrainName] walks into every fire knowing: who you are (from `CLAUDE.md` — the file she reads at the start of every conversation), the orgs and people in your workspace, your writing patterns (from `BRAND_VOICE.md`, learned from your sent emails), every decision you've logged, every commitment captured from your calls and emails, and per-project context for the workstreams.*
>
> ***Practical difference.*** *A vanilla morning brief gives you a generic 'here's your calendar' rundown. Your Command Room morning brief gives you 'you have [Person] at 2pm — he hasn't sent you anything in 28 days, you owe him the Q2 review since Wednesday, here's the opening line that lands hardest given your last 3 conversations.' Same prompt, completely different output, because [BrainName] is reading from your actual substrate.*
>
> ***The compounding effect.*** *Every meeting you process, every decision you log, every follow-up you send adds to the substrate. The longer you use Command Room, the more context exists, the sharper every scheduled task gets.*
>
> ***Future possibilities.*** *I'm setting up 5 scheduled tasks for you now — these cover the daily ritual. Later (after this onboarding), you can add more yourself or with [Operator]. Examples: a Monday morning prep specifically for your weekly [recurring 1:1] / first-of-the-month investor update draft / pre-call brief that fires 30 min before any meeting with [important person]. [BrainName] can also propose tasks based on patterns she notices.*
>
> ***Registering your 5 chats now:***
>
> *• `morning-brief` (7:30 AM weekdays)*
> *• `past-meetings` (9 AM weekdays)*
> *• `inbox-triage` (7 AM / 11 AM / 3 PM weekdays)*
> *• `upcoming-meetings` (5 PM weekdays)*
> *• `weekly-recap` (4 PM Fridays)*
>
> *...registering...*
> *All 5 registered. They appear in your Cowork 'Scheduled' section now. None will fire yet — [BrainName] is still scanning. We'll authorize them with a Run Now click later — you'll see each one produce real output before we wrap."*

**Customer-facing task-name vs registered taskId mapping.** The customer reads the 5 names above in M1 onboarding copy. The actual registered taskIds (what shows in the Cowork sidebar as `description`) are: `morning-brief` ("Morning Brief - Command Room") / `past-meetings` ("Past Meetings - Command Room") / `inbox` ("Inbox - Command Room") / `upcoming-meetings` ("Upcoming Meetings - Command Room") / `friday-wrap` ("Friday Wrap - Command Room"). The `inbox-triage` and `weekly-recap` spelling is M1 customer-facing copy; the canonical taskIds stay back-compat-stable.

**Cron times in the explainer above are the M1 customer-facing target.** The actual `DEFAULT_SCHEDULES` in `shared/scripts/schedule_config.py` may differ until cron values are re-anchored in a follow-up patch (currently: `morning-brief` 7 AM, `past-meetings` 5 PM, `inbox` 7:15 AM, `upcoming-meetings` 6:30 AM, `friday-wrap` 4 PM Fri). Phase 2's `load_schedule_config()` reads whatever's in DEFAULT_SCHEDULES; any drift between explainer copy and actual cron is captured in the M1 build log for follow-up.

**OPERATOR (verbal, in Chat 2, after the explainer lands):** *"Take a minute on that. The substrate point is the most important thing here — it's why this stack is different from any of the AI tools you've tried. Anything jump out?"*

~60–90 sec of operator-customer discussion in Chat 2 while light scan continues in Chat 1. Then the operator cues the customer back to Chat 1.

After this phase, proceed to Phase 0 (workspace discovery).

---

## v2.10.2 changes from v2.9.x / v2.10.1

- **Renamed** 4 of the 6 daily tasks for executive clarity:
  - `cr-meetings-today` → `cr-upcoming-meetings` (display: **Upcoming Meetings**)
  - `cr-inbox-pulse` → `cr-inbox` (display: **Inbox**)
  - `cr-cracks-watch` → `cr-dont-forget` (display: **Pulse**)
  - `cr-meetings-processed` → `cr-past-meetings` (display: **Past Meetings**)
- **Merged** the two commitment tasks (`cr-commitment-nudge` + `cr-commitment-chase`) into a single `cr-commitments` (display: **Commitments**). Both directions now surface in one chat thread with global numbering and direction-aware per-item actions. See `references/orchestrator-commitments.md`.
- **Added** `cr-historical-backfill-N` (one-shot tasks) for Phase 1.5 of onboarding. Chunked over time-of-day to ingest the user's last 12 months at metadata-only level without blowing context. Registered by `command-room-onboarding` (not by this skill on its own — see "Onboarding integration" below).
- **Universal chat-output rules** moved to `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Every orchestrator references it instead of restating rules.

## Verified contract (Cowork investigation, 2026-04-28)

- API: `create_scheduled_task(taskId, prompt, description, cronExpression?, fireAt?, recurrence?, notifyOnCompletion?)`
- Cron: 5-field, **LOCAL time** (not UTC). Step+range syntax supported.
- One-shot: `recurrence: "once"` with `fireAt: "<ISO>"` instead of `cronExpression`. Used for historical backfill chunks.
- Prompt: arbitrary chat string, NOT a skill trigger. Each fire = fresh Claude session.
- Jitter: 60-400 second deterministic dispatch jitter.
- Persistence: filesystem + events.jsonl only. No in-memory state across runs.
- 1 taskId = 1 persistent chat thread in Cowork's Scheduled section. Each fire appends a turn to that thread.
- **One-time install ritual:** every new taskId blocks on a manual tool-permission grant on first fire. M clicks Run Now once per task to authorize. Subsequent fires autonomous.

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

  > *"I can't find a Command Room workspace folder connected to this Cowork session. Make sure your workspace folder is connected in Cowork's Settings → Folders, then run `set up command room schedules` again."*

  Do NOT register tasks. The bootloaders would have nothing to bind to.

- **1 candidate:** show + confirm:

  > *"I'll register your scheduled tasks to write to: `<full_path>` (basename: `<BASENAME>`, last activity: `<MTIME>`, `<EVENTS_COUNT>` events recorded). Confirm with `yes`, or paste a different absolute path if this isn't your intended workspace."*

  Default to `yes` if the customer doesn't reply within the prompt. Save `WORKSPACE_BASENAME = <BASENAME>` for Phase 1.B substitution.

- **2+ candidates:** show numbered list, ask customer to pick:

  > *"I see <N> workspaces with `_hq/data/events.jsonl` connected to Cowork. Which one should your scheduled tasks bind to?*
  >
  > *1) `<basename1>` — `<full_path1>` (last activity: `<MTIME1>`, `<EVENTS_COUNT1>` events)*
  > *2) `<basename2>` — `<full_path2>` (last activity: `<MTIME2>`, `<EVENTS_COUNT2>` events)*
  > *...*
  >
  > *Reply with `1`, `2`, etc. — or paste a custom absolute path."*

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

**Legacy task migration (v2.9-v2.10.1 → v2.10.2):**

For each legacy taskId found in the user's existing schedule, DISABLE it via `update_scheduled_task(enabled: false)` and surface in the install summary as "migrated to [new name]":

| Legacy taskId | Action | New taskId |
|---|---|---|
| `cr-meetings-today` | disable + register | `upcoming-meetings` |
| `cr-inbox-pulse` | disable + register | `inbox` |
| `cr-commitment-nudge` | disable + register | `commitments` (merged) |
| `cr-commitment-chase` | disable + register | `commitments` (merged) |
| `cr-cracks-watch` | disable + register | `pulse` |
| `cr-meetings-processed` | disable + register | `past-meetings` |
| `cr-refresh-workspace-map` | **disable** (v2.14.25 — task removed from active set; surface "Removed daily Workspace Map auto-refresh — manual ↻ Refresh button on the artifact still works") | (none — task is gone) |
| `cr-upcoming-meetings` | **disable** (v2.14.27 — taskId rename; old "Cr upcoming meetings" title replaced by clean "Upcoming meetings" via new `upcoming-meetings` taskId) | `upcoming-meetings` |
| `cr-inbox` | **disable** (v2.14.27 — taskId rename) | `inbox` |
| `cr-commitments` | **disable** (v2.14.27 — taskId rename) | `commitments` |
| `cr-dont-forget` | **disable** (v2.14.27 — taskId rename to align with display name "Pulse"; events.jsonl history at source_skill='cr-dont-forget' preserved as append-only history) | `pulse` |
| `cr-past-meetings` | **disable** (v2.14.27 — taskId rename) | `past-meetings` |
| `cr-folder-bind-test` | **disable** (v2.14.27 — Cowork diagnostic test task left over from 2026-05-06 Q10/Q11 round; safe to disable, never intended to fire) | (none — diagnostic artifact) |
| `cr-folder-bind-test-2` | **disable** (v2.14.27 — Cowork diagnostic test task left over from 2026-05-06 Q10/Q11 round; safe to disable, never intended to fire) | (none — diagnostic artifact) |

(No delete API exists in the scheduled-tasks MCP; disable is the safe operation. Disabled tasks remain in the user's Scheduled section as historical reference but won't fire. v2.14.27 customers running `set up command room schedules` will see ~13 disabled tasks accumulate in their sidebar — surface this in the install summary so it's not a surprise. Filesystem surgery is the only way to make the sidebar truly clean: quit Cowork, edit `scheduled-tasks.json` to remove disabled entries, optionally delete the corresponding `Documents/Claude/Scheduled/<taskId>/` folders, restart.)

**Existing-taskId handling (v2.14.21+ — self-refresh with explicit verification):**

The canonical taskId → orchestrator-file mapping. **Use this dict literally — do NOT improvise filenames or display names from your own knowledge of the task list.** This dict is the only authoritative source for which orchestrator file backs which taskId:

```python
ORCHESTRATOR_MAP = {
    "morning-brief":     "orchestrator-morning-brief.md",  # Wraps the morning-briefing skill. Registered on first install.
    "upcoming-meetings": "orchestrator-upcoming-meetings.md",
    "inbox":             "orchestrator-inbox.md",
    "commitments":       "orchestrator-commitments.md",
    "pulse":             "orchestrator-dont-forget.md",   # display: "Pulse - Command Room"; orchestrator filename stays as `orchestrator-dont-forget.md` for events.jsonl source_skill back-compat (events written historically with source_skill='cr-dont-forget' remain valid as append-only history; new events post-v2.14.27 use source_skill='pulse')
    "past-meetings":     "orchestrator-past-meetings.md",
    "friday-wrap":       "orchestrator-friday-wrap.md",   # NEW v3.11.0. Wraps the weekly-recap skill. Registered on first install. First weekly-rhythm task.
}
# Seven tasks total — all user-facing chats. v2.14.27+ uses bare taskIds (no `cr-` prefix) so Cowork's sidebar title formatting renders cleanly: `inbox` → "Inbox", `commitments` → "Commitments", etc. Pre-v2.14.27 used `cr-*` prefix which displayed as "Cr inbox" / "Cr commitments" — the cr- prefix looked like a typo in the title. cr-refresh-workspace-map was REMOVED in v2.14.25. friday-wrap ADDED in v3.11.0 — first weekly-rhythm scheduled task.
#
# First-install gating: on a FRESH workspace (workspace_config.json missing or empty registered_taskIds), only the subset in `shared/scripts/schedule_config.py FIRST_INSTALL_TASK_IDS` registers ({morning-brief, upcoming-meetings, past-meetings, inbox, friday-wrap} as of M1 2026-05-23; inbox added in M1 — pre-M1 was 4 tasks). The 2 remaining entries above (commitments, pulse) stay in the map for re-runs / management flows but are NOT auto-registered day 1. See Phase 3 first-install branching. (v3.17.0: the silent `cleanup` maintenance task is ALSO in FIRST_INSTALL_TASK_IDS and registers on first install — but via **Step 1.D below**, separately, because it is not a chat-orchestrator and is intentionally absent from this ORCHESTRATOR_MAP.)
```

**Critical mismatch warnings:**

- **(v2.14.20 regression)** If you find a registered task with `taskId == "cr-pulse"` or `cr-dont-forget`, that's pre-v2.14.27 state — disable per the legacy migration table and register `pulse` per the new map.
- **(v2.14.27 rename)** If you find any of `cr-upcoming-meetings`, `cr-inbox`, `cr-commitments`, `cr-dont-forget`, or `cr-past-meetings` registered, those are v2.14.21-v2.14.26 taskIds — disable per the legacy migration table and register the bare-name equivalents (`upcoming-meetings`, `inbox`, etc.) fresh.
- **(stub regression)** If any registered task's prompt body is shorter than 1000 chars or doesn't contain `"OUTPUT CONTRACT (v2.13.0+ — MANDATORY)"` in its first 1500 characters AND doesn't contain `"# Scheduled task bootloader"` in its first 200 chars — that indicates a stub was registered instead of the canonical bootloader. Re-register fresh.

**Per-task processing (v2.14.24+ — bootloader pattern; run for every taskId in `ORCHESTRATOR_MAP`):**

**v2.14.24 architecture change.** Prior to v2.14.24, registration pinned the FULL canonical orchestrator body (~366 lines per task) into Cowork's scheduled-tasks DB. That solved the v2.14.20 stub-improvisation bug, but produced a different one: every plugin upgrade that changed orchestrator content required a re-registration, and customers who never re-ran `set up command room schedules` after upgrading kept firing stale prompts. Multiple early users hit it.

v2.14.24+ pins a tiny ~50-line **bootloader** instead. The bootloader resolves `$PLUGIN_ROOT` at fire time, reads the canonical `orchestrator-<name>.md` from the currently-installed plugin via `bash cat`, and executes it verbatim. Plugin upgrades propagate automatically. Drift is structurally impossible.

The canonical bootloader template lives at `skills/enable-command-room-schedules/references/scheduled-task-bootloader.md`. Read its top-of-file commentary for the full design rationale — the live evidence (cr-bootloader-test fire, plugin UUID stability check, frontmatter doubling test) that drove each design choice.

**Step 1.A — Verify orchestrator files exist and carry the contract marker (still mandatory at registration time, even though the body itself is no longer pinned).**

The bootloader assumes the orchestrator files exist on disk and contain the OUTPUT CONTRACT marker. Registration verifies this BEFORE composing the bootloader, so a partial / corrupt plugin install can't quietly register bootloaders that will fail at every fire:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -dt "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null | head -1 | sed 's:/$::')
cd "$PLUGIN_ROOT" && python3 -c "
import json
from pathlib import Path
ORCHESTRATOR_MAP = {
    'morning-brief': 'orchestrator-morning-brief.md',
    'upcoming-meetings': 'orchestrator-upcoming-meetings.md',
    'inbox': 'orchestrator-inbox.md',
    'commitments': 'orchestrator-commitments.md',
    'pulse': 'orchestrator-dont-forget.md',
    'past-meetings': 'orchestrator-past-meetings.md',
    'friday-wrap': 'orchestrator-friday-wrap.md',  # NEW v3.11.0 — weekly recap
}
ref_dir = Path('skills/enable-command-room-schedules/references')
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

If any assertion fails, ABORT the whole skill with a plain-English error: *"One of the Command Room files isn't readable right now. Please reinstall the plugin via `/plugin marketplace add chaletteholdings/commandroom<latest>` and try again."* Never register a partial set.

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
    'commitments': 'orchestrator-commitments.md',
    'pulse': 'orchestrator-dont-forget.md',
    'past-meetings': 'orchestrator-past-meetings.md',
    'friday-wrap': 'orchestrator-friday-wrap.md',  # NEW v3.11.0 — weekly recap
}
workspace_basename = os.environ.get('WORKSPACE_BASENAME', '').strip()
assert workspace_basename, 'WORKSPACE_BASENAME env var not set — Phase 0 did not run or did not surface a customer choice; ABORT'
assert '/' not in workspace_basename and '\\\\' not in workspace_basename, f'WORKSPACE_BASENAME must be a basename (no path separators), got: {workspace_basename!r}; ABORT'
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
            .replace('<WORKSPACE_BASENAME>', workspace_basename))
    # Hard sanity checks — never register a bootloader with unsubstituted placeholders.
    assert '<TASK_ID>' not in body, f'{task_id} bootloader has unsubstituted <TASK_ID> placeholder; ABORT'
    assert '<ORCHESTRATOR_FILENAME>' not in body, f'{task_id} bootloader has unsubstituted <ORCHESTRATOR_FILENAME> placeholder; ABORT'
    assert '<WORKSPACE_BASENAME>' not in body, f'{task_id} bootloader has unsubstituted <WORKSPACE_BASENAME> placeholder; ABORT'
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

**Why this matters (v2.14.24 architecture).** Pre-v2.14.20 the spec said "load the prompt from references/orchestrator-<name>.md" as prose, and agents wrote summaries instead — bug. v2.14.21 made the load a hard contract via bash + Python, fixing that bug but producing the drift bug. v2.14.24's bootloader closes the drift bug structurally — fires read from disk fresh every time, so a plugin upgrade is enough to update fire behavior. **Customers no longer have to re-run `set up command room schedules` after every plugin upgrade.** The hard read happens at fire time, not at registration time.

**Step 1.D — Register the silent weekly maintenance task (`cleanup`, v3.17.0+).**

`cleanup` is NOT a chat-orchestrator (no widget OUTPUT CONTRACT, no `orchestrator-*.md`) — it is silent Sunday self-maintenance + brain self-heal. Register it SEPARATELY from the 7 chats above, with a short skill-invocation prompt instead of a bootloader-over-an-orchestrator. Its cron comes from `schedule_config.py` DEFAULT_SCHEDULES["cleanup"] (`0 18 * * 0` — 6 PM Sundays). It IS in FIRST_INSTALL_TASK_IDS, so it registers on fresh installs AND re-runs.

```bash
cd "$PLUGIN_ROOT" && WORKSPACE_BASENAME="$WORKSPACE_BASENAME" python3 -c "
import os
basename = os.environ['WORKSPACE_BASENAME'].strip()
assert basename and '/' not in basename, 'WORKSPACE_BASENAME must be a bare basename from Phase 0; ABORT'
prompt = (
    '# Command Room — weekly cleanup (silent Sunday self-maintenance)\n\n'
    'Run the cleanup skill end-to-end for the Command Room workspace whose folder '
    'basename is ' + basename + '. Resolve the workspace per CONTRACT.md Rule 22 '
    '(find _hq/ under the mount matching that basename), then follow EVERY phase of '
    'skills/cleanup/SKILL.md in order: Phase 1 silent scan, Phase 2 auto-fix sweep, '
    'Phase 3 substrate integrity (detect + heal), Phase 3.5 brain self-heal (Live State '
    'render + idempotent migration), Phase 4 the three-beat Monday note '
    '(what I tidied / what I handled for you / what is waiting on you), Phase 5 '
    '(save the .docx only when substantive). Stay silent unless something needs the '
    'CEO eyes. This is a maintenance note, NOT a chat-widget.'
)
print(len(prompt), 'char cleanup prompt composed for basename:', basename)
"
```

Then register it: `create_scheduled_task(taskId='cleanup', prompt=<the composed prompt above>, cronExpression='0 18 * * 0', description='Weekly self-maintenance + brain self-heal (silent)', notifyOnCompletion=false)`. **Idempotent:** if a `cleanup` task already exists with a matching prompt, skip; if it differs, `update_scheduled_task(taskId='cleanup', prompt=...)` preserving cron/enabled. Surface in the install summary: `Registered the weekly cleanup (silent Sunday maintenance + brain self-heal)`. Because the prompt fires the skill (which reads from the installed plugin), plugin upgrades propagate automatically — no re-registration needed when cleanup's logic changes.

**Step 1.E — Register the silent daily sent-mail reconciliation task (`reconcile-sent`, v3.18.12+).**

`reconcile-sent` is the same shape as `cleanup` — a silent, skill-invoking background task, NOT a chat-orchestrator (no widget, no `orchestrator-*.md`). Register it SEPARATELY from the 7 chats and from cleanup, with a short skill-invocation prompt. Its cron comes from `schedule_config.py` DEFAULT_SCHEDULES["reconcile-sent"] (`45 6 * * 1-5` — 6:45 AM weekdays, deliberately BEFORE morning-brief at 7:00 so the brief reads an already-reconciled substrate). It IS in FIRST_INSTALL_TASK_IDS, so it registers on fresh installs AND re-runs. (Why its own task and not folded into the brief: Bug #98-v3 — three folds were skipped because an invisible substrate write loses to a visible deliverable; it needs to BE the job.)

```bash
cd "$PLUGIN_ROOT" && WORKSPACE_BASENAME="$WORKSPACE_BASENAME" python3 -c "
import os
basename = os.environ['WORKSPACE_BASENAME'].strip()
assert basename and '/' not in basename, 'WORKSPACE_BASENAME must be a bare basename from Phase 0; ABORT'
prompt = (
    '# Command Room — reconcile sent mail (silent daily maintenance)\n\n'
    'Run the reconcile-sent skill end-to-end for the Command Room workspace whose '
    'folder basename is ' + basename + '. Resolve the workspace per CONTRACT.md '
    'Rule 22 (find _hq/ under the mount matching that basename), then follow '
    'skills/reconcile-sent/SKILL.md in order: read the cursor, do a REAL in:sent '
    'fetch since the cursor, call reconcile_and_receipt, and self-validate via '
    'validate_reconcile_ran that the sent_reconcile audit event actually landed. '
    'Stay silent unless something closed (then one undo-affordance line). This is '
    'silent maintenance, NOT a chat-widget.'
)
print(len(prompt), 'char reconcile-sent prompt composed for basename:', basename)
"
```

Then register it: `create_scheduled_task(taskId='reconcile-sent', prompt=<the composed prompt above>, cronExpression='45 6 * * 1-5', description='Daily sent-mail reconciliation (silent)', notifyOnCompletion=false)`. **Idempotent:** if a `reconcile-sent` task already exists with a matching prompt, skip; if it differs, `update_scheduled_task(taskId='reconcile-sent', prompt=...)` preserving cron/enabled. Surface in the install summary: `Registered the daily sent-mail reconciliation (silent, runs before your morning brief)`. Plugin upgrades propagate automatically — the prompt fires the skill, which reads from the installed plugin.

### Cowork bug awareness — `update_scheduled_task` and #40835

[anthropic/claude-code#40835](https://github.com/anthropics/claude-code/issues/40835) (open as of 2026-05-06): creating or modifying a scheduled task may disable MCP connectors in OTHER existing scheduled tasks. The issue body says "creation/modification" so `update_scheduled_task` is plausibly affected too. There is no per-task MCP-status field exposed in `list_scheduled_tasks` to verify after-the-fact.

**Mitigation:** after any registration / update batch, surface this exact line in the install summary so the customer knows to manually re-prime each task's connector cache:

> *"One quick thing: setting up new scheduled tasks can temporarily turn off connectors in your other tasks. Open each scheduled chat in Cowork once and confirm any permission prompts. After that, they'll run on schedule with full access."*

If you're running this skill from inside an in-progress onboarding (Phase 4 of `command-room-onboarding`), defer this re-prime step to the operator-delivered Meeting 1 hand-off — DON'T blast the customer with it during their first install.

If much-older v2.8.x tasks present (`cr-refresh-*`, `cr-daily-morning-pack`, `cr-workflow-commitment-chase-drafts`, `cr-workflow-weekly-audit`), DISABLE those too. Same for any `cr-pulse` or `cr-dont-forget` registrations encountered — both are pre-v2.14.27 state; canonical taskId is bare `pulse` per the migration table above.

## Phase 2 — Load schedule config (v2.14.10+)

Read the per-workspace schedule configuration via the `schedule_config` helper. The helper merges defaults with any overrides stored in entities.json `workspace.schedule_config`:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
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

**First install behavior:** entities.json typically has no `workspace.schedule_config` field on first install — the helper returns built-in defaults silently. After install, M (operator) or the user runs `change my schedule` to tune.

**Disabled tasks:** if `config[taskId].enabled` is `false`, skip the registration entirely (or call `update_scheduled_task` with `enabled: false` if currently registered). Disabled tasks remain in the user's Scheduled section as historical reference but won't fire.

**Defaults (built-in fallbacks if config absent):**
- **Time zone:** detected from entities.json primary user (`person.time_zone` field if set), else system time zone
- **Per-chat times:** see `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES` (Morning Brief 7 AM / Upcoming 6:30 AM / Inbox 7 AM / Commitments 8:30 AM / Pulse 9 AM / Past Meetings 5 PM, weekdays; Friday Wrap 4 PM Fridays)

## Phase 3 — Register or refresh the 7 orchestrators (6 daily widgets + 1 weekly recap)

**First-install gate (M1 / 2026-05-23+):** before iterating ORCHESTRATOR_MAP, decide which subset of taskIds gets registered:

```python
from schedule_config import FIRST_INSTALL_TASK_IDS

if FIRST_INSTALL:
    # Fresh workspace per Phase 0.C detection. Register ONLY the 5 M1 first-install tasks.
    # The 2 remaining (commitments / pulse) are added later via operator-driven follow-up
    # sessions — pulse benefits from accumulated workspace signal; commitments lands once
    # the customer has been logging meetings for a couple of weeks.
    tasks_to_register = {
        tid: fname
        for tid, fname in ORCHESTRATOR_MAP.items()
        if tid in FIRST_INSTALL_TASK_IDS
    }
else:
    # Existing workspace. Re-run / refresh — preserve whatever the customer already has
    # registered (do NOT auto-disable commitments/pulse if they're already running)
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
- Customer says `add commitments` / `add pulse` in Phase 6 management flow → those taskIds get registered individually.

Per Phase 1's `ORCHESTRATOR_MAP`, each taskId in `tasks_to_register` goes through one of three paths based on detection:

- **Not yet registered** → call `mcp__scheduled-tasks__create_scheduled_task` with `prompt=body` from Phase 1's read step. Full registration.
- **Already registered with stale prompt (hash mismatch)** → call `mcp__scheduled-tasks__update_scheduled_task(taskId, prompt=body)`. Refresh in place. Cron, description, enabled, notify all preserved.
- **Already registered with current prompt (hash match)** → skip. No-op idempotent.

**Pass the full `body` string from Phase 1 as the `prompt` parameter. NEVER paraphrase, summarize, or extract a "mission section" instead.** The orchestrator IS the work — there's no separate runner code; the Claude session executes everything from the prompt. Loss of the v2.13.0 OUTPUT CONTRACT preamble (which lives in the first ~50 lines of every chat-emitting orchestrator) means the fire bypasses every validator + the renderer + the STOP CONTRACT enforcement chain. That's the v2.14.20 regression this v2.14.21 spec exists to prevent.

**Per-task registration template (v2.14.10+ config-driven):**

For each taskId below, pull `cron` + `label` from the Phase 2 config map. Build the `description` parameter by combining the display name + the config's `label` field. Example:

```python
config = load_schedule_config(...)  # from Phase 2
task_id = "inbox"
spec = config[task_id]               # {"cron": "0 7 * * 1-5", "label": "7 AM weekdays", "enabled": True}
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
- `cronExpression`: from config (default `"0 7 * * 1-5"`)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body at `references/orchestrator-inbox.md`.

### Schedule 3 — Commitments

- `taskId: "commitments"` (v2.14.27+ — bare taskId; prior cr-commitments → migration disabled)
- `description`: **`"Commitments - Command Room"`** (v2.14.25+ canonical display name)
- `cronExpression`: from config (default `"30 8 * * 1-5"`)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body at `references/orchestrator-commitments.md`.

### Schedule 4 — Pulse

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
- `cronExpression`: from config (default `"0 16 * * 5"`). 4 PM Fridays — first weekly-rhythm scheduled task; all prior tasks are daily. Empty Friday-PM slot avoids collision with the AM-heavy block.
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body lives at `references/orchestrator-friday-wrap.md` and is read fresh by the bootloader at fire time. The orchestrator wraps the existing `weekly-recap` skill — keeps the scheduled-fire output and the on-demand `weekly recap` / `recap last week` output convergent (one source of truth for the recap format and `.docx` save path).

### v2.14.25+ — Schedule 7 (Workspace Map refresh) DROPPED

The v2.14.11+ daily auto-refresh scheduled task `cr-refresh-workspace-map` is REMOVED from the active task set as of v2.14.25. Per M's call: the daily auto-rebuild of the Workspace Map sidebar artifact wasn't worth the operational complexity (one more task to register, one more cron to fire, one more #40835 risk surface).

**What remains:** the Workspace Map artifact itself stays fully functional. Customers install via `enable workspace map` / `install workspace map`; the artifact's manual `↻ Refresh` button still triggers an ad-hoc rebuild on click; on-demand commands like `rebuild workspace map` still work. Only the daily auto-refresh cron at 4 PM weekdays is gone.

**Migration for existing customers (M, Sam, anyone with the task already registered):** Phase 1's legacy-taskId migration list (above) now includes `cr-refresh-workspace-map`. On next `set up command room schedules` run, the task is DISABLED via `update_scheduled_task(enabled: false)` and surfaced in the install summary as: *"Removed daily Workspace Map auto-refresh — manual `↻ Refresh` button on the artifact still works."*

For each created schedule, log a `schedule_created` event:
```jsonl
{"type":"schedule_created","ts":"<ISO>","data":{"taskId":"<id>","cron":"<expr>","label":"<description>"}}
```

## Phase 3.5 — Post-registration verification (v2.14.24+ — bootloader pattern)

After every `create_scheduled_task` / `update_scheduled_task` call in Phase 3, **read back what's now registered and verify it's the canonical bootloader (not an agent-improvised stub).** This is the hard gate that catches v2.14.20-style improvisation regressions, adapted for the bootloader pattern.

What's verified:
1. Each registered taskId's prompt CONTAINS the canonical bootloader markers (`# Scheduled task bootloader`, `Resolve the plugin path`, `Read the orchestrator and execute it verbatim`).
2. Each registered taskId's prompt CONTAINS the correct task name (`<TASK_ID>` was substituted with the actual taskId, not left as a literal placeholder).
3. Each registered taskId's prompt CONTAINS the correct orchestrator filename (`<ORCHESTRATOR_FILENAME>` was substituted with the file from `ORCHESTRATOR_MAP`).
4. Each registered taskId's prompt does NOT start with `---` frontmatter (Cowork prepends its own; user-supplied frontmatter creates a doubling bug).
5. Each registered taskId's prompt is between 1500 and 3500 chars (bootloaders should be ~50 lines / ~2500 chars; significantly outside that range means the agent improvised a stub or somehow registered the full orchestrator body).

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
# not the full ORCHESTRATOR_MAP. On first-install runs the 3 deferred tasks
# (inbox/commitments/pulse) are intentionally NOT registered — they're not failures.
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
        # Size sanity check — v2.14.26 bootloaders are ~5500 chars (workspace logic + fallback discovery + ABORT messages), full orchestrators ~10K+ chars
        if len(actual_prompt) > 7000:
            failures.append(f"{task_id}: registered prompt is {len(actual_prompt)} chars — too large for a bootloader. Did the full orchestrator body get registered by mistake?")
            continue
        if len(actual_prompt) < 2500:
            failures.append(f"{task_id}: registered prompt is only {len(actual_prompt)} chars — too small for a canonical v2.14.26 bootloader. Stub-improvisation regression?")
            continue
```

**Surface failures in plain English.** If any task fails verification:

> *"Couldn't finish setting up: [list]. Re-running this skill will retry. If it keeps failing, re-install via `/plugin marketplace add chaletteholdings/commandroom<latest>`."*

**Display name verification (v2.14.25+):** also verify each registered task's `description` field matches the canonical "X - Command Room" format. Specifically:
- `morning-brief` → `"Morning Brief - Command Room"`
- `upcoming-meetings` → `"Upcoming Meetings - Command Room"`
- `inbox` → `"Inbox - Command Room"`
- `commitments` → `"Commitments - Command Room"`
- `pulse` → `"Pulse - Command Room"`
- `past-meetings` → `"Past Meetings - Command Room"`
- `friday-wrap` → `"Friday Wrap - Command Room"` (NEW v3.11.0)

If the description doesn't match, call `update_scheduled_task(taskId, description=<canonical>)` to fix in place. Display-name drift is a regression class on its own.

**Why this gate exists, adapted for v2.14.24.** Pre-v2.14.21, registration was prose-instructed and agents improvised stubs. v2.14.21 fixed that by making registration a hard contract that pins the FULL orchestrator body. v2.14.24 swaps the pinned content from "full body" to "bootloader" — but the agent-improvisation risk doesn't go away. The agent could still improvise a stub bootloader if the registration step is loose. So Phase 3.5 verifies the registered prompt has the canonical markers + correct substitutions + frontmatter-clean + reasonable size. v2.14.25 adds display-name verification too. Catches the same class of bug, scoped to the new pattern.

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
- `prompt`: see `references/orchestrator-historical-backfill.md` *(NOTE: this file should be created in v2.10.2 if not present yet — defer to a future patch if absent at install time and surface a plain-English note: "Historical backfill orchestrator not yet shipped — top-12-month context will fill in via daily scheduled tasks instead.")*

The prompt receives the chunk window (start/end dates) as part of the orchestrator's input. Each chunk's session:
- Fetches metadata for the window from every connector
- Writes events.jsonl in batched appends (no body content, just metadata)
- Creates provisional person + project records for clusters
- Updates the resume marker (`_hq/data/.backfill_cursor`)
- Exits cleanly

### Step 3: log the schedule

For each chunk:
```jsonl
{"type":"backfill_chunk_scheduled","ts":"<ISO>","data":{"chunk_n":N,"of":M,"fireAt":"<ISO>","window_start":"<date>","window_end":"<date>","tier":"<light|medium|heavy>"}}
```

### Step 4: surface to user as part of onboarding close-out

The onboarding skill (NOT this skill) handles the user-facing close-out. This skill just registers the chunks silently and returns the count.

## Phase 5 — Surface install ritual + confirmation

The summary block branches on `FIRST_INSTALL` (set in Phase 0.C).

### If `FIRST_INSTALL = True` (M1 default — 5 tasks registered)

```
Command Room schedules registered:

✓ Morning Brief        (Mornings, weekdays — fires before your workday)
✓ Upcoming Meetings    (Mornings, weekdays — preps the day ahead)
✓ Inbox                (Throughout the day, weekdays — surfaced as `inbox-triage` in onboarding copy)
✓ Past Meetings        (Late afternoon, weekdays — processes the day's calls)
✓ Friday Wrap          (Friday afternoons — weekly recap, surfaced as `weekly-recap` in onboarding copy)

ONE-TIME INSTALL RITUAL — required by Cowork:

5 manual permission grants needed. Open Cowork's Scheduled section in
the sidebar. You'll see 5 task entries with yellow dots (pending
permission). Tap "Run Now" on each to authorize tool access.

  1. Morning Brief
  2. Upcoming Meetings
  3. Inbox
  4. Past Meetings
  5. Friday Wrap

(The tasks then start firing on their normal schedule.)

2 more daily chats — Commitments and Pulse — will be added in a
follow-up session once you've been logging meetings for a couple of
weeks. They benefit from accumulated workspace signal before they fire
well.

To manage anytime: `list my schedules`, `pause [taskId]`, `disable [taskId]`,
or re-run `set up command room schedules` to add the remaining 2.
```

### If `FIRST_INSTALL = False` (existing workspace — refresh / add flow)

```
Command Room schedules registered:

✓ Upcoming Meetings (6:30 AM weekdays)
✓ Inbox             (7:00 AM weekdays)
✓ Commitments       (8:30 AM weekdays — both directions in one chat)
✓ Pulse             (9:00 AM weekdays)
✓ Past Meetings     (5:00 PM weekdays)
✓ Morning Brief     (7:00 AM weekdays)
✓ Friday Wrap       (4:00 PM Fridays — added by re-run, NEW v3.11.0)

[If Morning Brief was just added by this re-run, surface:]
Morning Brief is new (added 2026-05-17). If 7 AM collides with your
Inbox slot, run `change-schedule` to move Inbox to 7:15 AM.

[If Friday Wrap was just added by this re-run, surface:]
Friday Wrap is new (v3.11.0). First weekly-rhythm task — fires Fridays at
4 PM, wraps the 7 days into a recap (.docx saved + inline). Don't forget
to authorize it on its first fire.

[If migrating from v2.9-v2.10.1, add:]
Migrated from prior version:
  • Meetings Today → Upcoming Meetings
  • Inbox Pulse → Inbox
  • Commitments You Owe + Commitments Owed To You → Commitments (merged)
  • Cracks Watch → Pulse (taskId pulse)
  • Meetings Processed → Past Meetings
The legacy entries are disabled — your scheduled chats now live under the new names.

[If migrating from a v2.14.20 broken state where cr-pulse or cr-dont-forget was registered, add:]
Repaired v2.14.20-v2.14.26 registration: disabled legacy pulse-orchestrator task and registered the canonical bare-name `pulse` with the current orchestrator.

ONE-TIME INSTALL RITUAL — required by Cowork (only for tasks added this run):

Each newly-registered task needs a manual permission grant on its first
fire. Open Cowork's Scheduled section in the sidebar. Click "Run Now"
on any task with a yellow dot to authorize tool access. (Tasks you've
already authorized stay authorized.)

To manage anytime: `list my schedules`, `pause [taskId]`, `disable [taskId]`.
```

## Phase 5.9 — Cleanup-registration assertion (v3.18.2+, Bug #82 — UNCONDITIONAL, runs before any Phase 6 early-exit)

**This check is mandatory on EVERY invocation, including the re-run / "already configured" path.** Before surfacing the Phase 6 management prompt (or taking any "all current — nothing to do" early-exit), you MUST verify the silent `cleanup` task is registered.

**Why this is its own gate.** The idempotency checks above iterate `ORCHESTRATOR_MAP` (the 7 chats). `cleanup` is intentionally NOT in that map — it is not a chat-orchestrator and registers separately via **Step 1.D**. So the "are all chats registered?" check is structurally blind to `cleanup`: on an existing workspace (all 7 chats present), the skill reports "all current" and routes straight to Phase 6, and **Step 1.D is never reached** — exactly the v3.18.1 failure (Bug #82). Every pre-v3.17.0 upgrader (clients ~v3.14.4) therefore never got the Sunday cleanup. `cleanup` IS in `FIRST_INSTALL_TASK_IDS`, so Phase 3's `target_set = existing | FIRST_INSTALL_TASK_IDS` already contains it — this gate just makes the assertion explicit and unconditional so no branch can skip it.

**Do this:**

1. Call `mcp__scheduled-tasks__list_scheduled_tasks`.
2. If **no** task with `taskId == "cleanup"` (cron `0 18 * * 0`) is present → **run Step 1.D now** to register it (idempotent — if it somehow exists with a stale prompt, Step 1.D updates in place). This is the cleanup analog of the Friday-Wrap generic-add path in `command-room-update-bridge` Phase 4.7.
3. Surface in the summary: `Registered the weekly cleanup (silent Sunday maintenance + brain self-heal)` — but only when this gate actually registered it (don't announce a no-op).
4. **Same assertion for the silent `reconcile-sent` task (v3.18.12+, Bug #98-v3).** It is the same class as `cleanup` — a silent non-chat task absent from `ORCHESTRATOR_MAP`, registered via **Step 1.E**, in `FIRST_INSTALL_TASK_IDS`. If no task with `taskId == "reconcile-sent"` is present → run Step 1.E now (idempotent). Surface `Registered the daily sent-mail reconciliation (silent, runs before your morning brief)` only when this gate actually registered it.
5. Only after BOTH assertions pass may you continue to Phase 6.

```python
# Pseudocode — translate to actual MCP calls.
registered_ids = {t["taskId"] for t in mcp__scheduled_tasks__list_scheduled_tasks()}
if "cleanup" not in registered_ids:
    run_step_1D()                       # register cleanup — NEVER skipped by the Phase 6 early-exit
    summary_lines.append("Registered the weekly cleanup (silent Sunday maintenance + brain self-heal)")
if "reconcile-sent" not in registered_ids:
    run_step_1E()                       # register reconcile-sent — same unconditional guarantee (Bug #98-v3)
    summary_lines.append("Registered the daily sent-mail reconciliation (silent, runs before your morning brief)")
```

## Phase 6 — Re-run / management

Re-firing this skill detects existing schedules. Surfaces:

> *"Command Room schedules already configured. [N] scheduled chats running. Want to add, change, remove, or reset? (add / change / remove / reset / nothing)"*

(`[N]` is the count of currently-enabled registered taskIds — typically 5 on a fresh M1 install, up to 7 once Commitments and Pulse have been added.)

- `add` — only useful if a future version adds new chats
- `change` — list existing, ask which + new cron. THIS is the calibration entry path (v2.9.2+ doesn't ask cadence questions at first install; explicit `change` request opens that conversation).
- `remove` — list existing, ask which to disable (no delete API)
- `reset` — disable everything + re-register all v2.10.2 (fresh state, defaults)
- `nothing` — exit silently

### Explicit calibration intent

If the user fires this skill with `customize my command room schedules` / `change my schedule cadence`, route directly into Phase 6 `change` flow without the "already configured" preamble.

## Reference files

The 7 chat-emitting orchestrator prompts live in `references/` (workspace-map refresh was retired in v2.14.25):

- `orchestrator-morning-brief.md` (taskId `morning-brief`; display "Morning Brief"; wraps the `morning-briefing` skill)
- `orchestrator-upcoming-meetings.md` (taskId `upcoming-meetings`)
- `orchestrator-inbox.md` (taskId `inbox`)
- `orchestrator-commitments.md` (taskId `commitments`)
- `orchestrator-dont-forget.md` (taskId `pulse`; display name "Pulse" — orchestrator filename kept for backward compat with events.jsonl `source_skill='cr-dont-forget'` history)
- `orchestrator-past-meetings.md` (taskId `past-meetings`)
- `orchestrator-friday-wrap.md` (taskId `friday-wrap`; display "Friday Wrap"; NEW v3.11.0 — wraps the `weekly-recap` skill; first weekly-rhythm scheduled task)

`orchestrator-refresh-workspace-map.md` exists in the references folder as a historical artifact only; it's not in `ORCHESTRATOR_MAP` and never registered.

**There is no `orchestrator-pulse.md` file** — Pulse content lives in `orchestrator-dont-forget.md`. If you find a registered task with `taskId: "cr-pulse"`, that's a v2.14.20 regression — disable it and register `cr-dont-forget` per the `ORCHESTRATOR_MAP` in Phase 1.

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
