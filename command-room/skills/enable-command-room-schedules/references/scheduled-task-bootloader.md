# Bootloader template for scheduled-task registration (v2.14.24+, v2.14.26+ workspace-aware, v3.2.1+ multi-plugin-aware)

This file is the canonical bootloader template registered into Cowork's scheduled-tasks DB at registration time. Each scheduled task gets a copy with `<TASK_ID>`, `<ORCHESTRATOR_FILENAME>`, and (v2.14.26+) `<WORKSPACE_BASENAME>` substituted.

## v2.14.26 — workspace binding via baked-in basename + fallback discovery

Per the Cowork diagnostic 2026-05-06: `userSelectedFolders` is NOT a passable parameter to `create_scheduled_task` / `update_scheduled_task` (silently dropped). Folder binding is implicit at fire time — Cowork mounts whatever folders are connected when the fire runs. v2.14.26+ works around this by:

1. **At registration time** (`enable-command-room-schedules` Phase 0+1): the skill runs workspace discovery via `find $SESSION_DIR/mnt -name events.jsonl`, presents matches to the customer, asks which workspace to bind, then bakes that folder's BASENAME into each task's bootloader as the `<WORKSPACE_BASENAME>` placeholder.
2. **At fire time:** the bootloader's Step 1 first tries `WORKSPACE="$SESSION_DIR/mnt/<baked_basename>"`. If that path has `_hq/data/events.jsonl`, use it. If not (folder renamed, customer connected different folder, etc.), fall back to discovery — `find -name events.jsonl` and pick the most-recently-modified candidate. If still nothing, abort with plain English asking the customer to connect the right folder.

This handles all three Cowork folder-binding semantics (snapshot-at-registration, snapshot-at-first-approval, live-at-fire) — the bootloader degrades gracefully from "explicit baked-in path" to "discover what's mounted" to "abort loudly."

## Why a bootloader, not the full orchestrator body

Pre-v2.14.24, registration pinned the full canonical orchestrator body (~366 lines) into Cowork's scheduled-tasks DB. That worked, but produced a class of bug: every plugin upgrade that changed orchestrator content required a re-registration, and customers who never re-ran `set up command room schedules` after upgrading kept firing stale prompts. M, Sam, and downstream customers all hit it.

v2.14.24+ pins a tiny ~50-line bootloader instead. The bootloader resolves `$PLUGIN_ROOT` at fire time, reads the canonical `orchestrator-<name>.md` from the currently-installed plugin via `bash cat`, and executes it verbatim. Plugin upgrades propagate automatically. Drift is structurally impossible.

## Why `bash cat`, not the Read tool

Live test 2026-05-06 (cr-bootloader-test fire) proved: the Read tool from a fired session **cannot reach** `/sessions/<adj>/mnt/.remote-plugins/plugin_*/...` paths — Read only sees the user's connected workspace folders. But `bash cat` from a fired session *can* reach the plugin clone via that path (verified by the cr-pulse fire's successful `python3 -c "from chat_output_renderer import ..."` import, which is the same code path).

So the bootloader uses `bash cat` to read the orchestrator. The output of `cat` returns to Claude as a tool result; Claude treats it as the rest of its instructions for the fire.

## Why `ls -dt`, not `ls -d`

Live evidence 2026-05-06 (workspace history): plugin upgrades can leave orphaned `plugin_<old-uuid>` directories alongside the new one for some interval. `ls -d plugin_*` returns them in directory-traversal order, which can pick the OLD one (the prior UUID `plugin_018VczLmZRt15g7jHP43Hc4W` came alphabetically before the current `plugin_01C6o4bN2U6VQVfuERYmtABu`). `ls -dt` sorts by mtime, picking the most recently mounted — which is the just-installed version. Fixes the upgrade-window race.

## Why iterate, not `ls -dt | head -1` (v3.2.1+)

Reported 2026-05-11: Sam + Bo (both on v3.0+ with current bootloader code) hit `ABORT_ORCHESTRATOR_NOT_FOUND` on `upcoming-meetings`. Root cause: `ls -dt | head -1` picks the most-recently-mounted plugin clone *period* — whatever plugin it is. If any non-Command-Room plugin (Anthropic skills marketplace, another Cowork plugin, etc.) was mounted more recently than Command Room, `PLUGIN_ROOT` resolves to that plugin's folder, the orchestrator file isn't there, abort fires.

Also covers the symptom mode of Cowork's VHD-cache bug (Session 56 archive): even after pushing a plugin update, the mounted plugin VHD may serve a stale or incomplete file tree until Cowork is fully restarted. If the cached snapshot is missing the orchestrator file at the exact path, the head-pick fails. Restart still required to refresh VHD — but the bootloader no longer silently picks a wrong-plugin clone in the meantime.

Fix: iterate the mtime-sorted list and pick the FIRST clone that actually contains the canonical skill structure (`skills/enable-command-room-schedules/references/<ORCHESTRATOR_FILENAME>`). Falls through gracefully when extra plugins are mounted; aborts loudly only when no Command Room clone exists at all.

## Substitution rules at registration time

The registration skill (`enable-command-room-schedules/SKILL.md` Phase 1) reads this template, substitutes:

- Every literal `<TASK_ID>` → the canonical taskId (e.g., `cr-inbox`)
- Every literal `<ORCHESTRATOR_FILENAME>` → the orchestrator filename from `ORCHESTRATOR_MAP` (e.g., `orchestrator-inbox.md`)
- (v2.14.26+) Every literal `<WORKSPACE_BASENAME>` → the basename of the customer-confirmed workspace folder. Cowork mounts the connected folder under `mnt/<basename>/`, so whatever the customer named their folder is what the basename becomes — could be anything. NEVER substitute a hardcoded folder name from these docs; resolve at runtime via `shared/CONTRACT.md` Rule 22 (`find $SESSION_DIR/mnt -name _hq`).

Then passes the substituted body to `create_scheduled_task` / `update_scheduled_task` as the `prompt` parameter.

**Frontmatter rule:** the bootloader body MUST NOT start with a `---` frontmatter block. Cowork prepends its own frontmatter; user-supplied frontmatter would create a doubling bug (verified live 2026-05-06).

## The bootloader template (everything below this heading is the registered prompt body)

# Scheduled task bootloader — <TASK_ID>

You are running the scheduled task `<TASK_ID>`. This SKILL.md is a bootloader, not the orchestrator. The canonical orchestrator content lives in the plugin folder and is read fresh at every fire — so plugin upgrades propagate automatically without re-registration.

## Step 1 — Resolve the plugin path (mtime-sorted, picks the most recently mounted clone)

Run this bash. It resolves THREE paths: the plugin clone (where the orchestrator file lives), the user's workspace (where `_hq/data/events.jsonl` lives — bound at registration time via the `<WORKSPACE_BASENAME>` placeholder), and a fallback workspace via discovery if the baked-in basename doesn't resolve at fire time:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
# Plugin clone — iterate mtime-sorted candidates, pick the FIRST that actually contains the canonical
# Command Room skill structure (v3.2.1+). Was `ls -dt | head -1` pre-v3.2.1, which silently picked
# whatever plugin was mounted last — broke when any other Cowork plugin was the most recent mount.
PLUGIN_ROOT=""
ORCH=""
for d in $(ls -dt "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null); do
  candidate="${d%/}/skills/enable-command-room-schedules/references/<ORCHESTRATOR_FILENAME>"
  if [ -f "$candidate" ]; then
    PLUGIN_ROOT="${d%/}"
    ORCH="$candidate"
    break
  fi
done
# Workspace — first try the baked-in basename (set at registration time when the customer
# confirmed which folder to use). If that path has the canonical _hq/data/events.jsonl, use it.
WORKSPACE="$SESSION_DIR/mnt/<WORKSPACE_BASENAME>"
if [ ! -f "$WORKSPACE/_hq/data/events.jsonl" ]; then
  # Baked-in path didn't resolve — fall back to discovery. Find any mounted folder that has
  # the canonical _hq/data/events.jsonl marker. Prefer most-recently-modified events.jsonl
  # as a mtime-based tiebreak when multiple workspaces are mounted.
  CANDIDATE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type f -name "events.jsonl" -path "*/_hq/data/*" 2>/dev/null | while read f; do echo "$(stat -c %Y "$f") $f"; done | sort -rn | head -1 | awk '{$1=""; print substr($0,2)}')
  if [ -n "$CANDIDATE" ]; then
    WORKSPACE=$(dirname "$(dirname "$(dirname "$CANDIDATE")")")
  else
    WORKSPACE=""
  fi
fi
echo "PLUGIN_ROOT=$PLUGIN_ROOT"
echo "ORCH=$ORCH"
echo "WORKSPACE=$WORKSPACE"
# Count Command Room candidates we found (clones that match the canonical skill structure).
# Differentiates "no plugin mounted at all" from "plugin mounted but the orchestrator file
# isn't where we expect" — the second case is usually Cowork's VHD-cache bug (stale mount).
CR_CANDIDATE_COUNT=0
for d in $(ls -dt "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null); do
  [ -f "${d%/}/skills/enable-command-room-schedules/references/<ORCHESTRATOR_FILENAME>" ] && CR_CANDIDATE_COUNT=$((CR_CANDIDATE_COUNT+1))
done
ANY_PLUGIN_COUNT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null | wc -l)
echo "CR_CANDIDATE_COUNT=$CR_CANDIDATE_COUNT"
echo "ANY_PLUGIN_COUNT=$ANY_PLUGIN_COUNT"
[ -n "$PLUGIN_ROOT" ] && [ -f "$ORCH" ] || {
  if [ "$ANY_PLUGIN_COUNT" -eq 0 ]; then
    echo "ABORT_PLUGIN_NOT_FOUND"
  else
    echo "ABORT_ORCHESTRATOR_NOT_FOUND"
  fi
}
[ -n "$WORKSPACE" ] && [ -f "$WORKSPACE/_hq/data/events.jsonl" ] || echo "ABORT_WORKSPACE_NOT_FOUND"
```

If the bash output contains `ABORT_PLUGIN_NOT_FOUND`, `ABORT_ORCHESTRATOR_NOT_FOUND`, or `ABORT_WORKSPACE_NOT_FOUND`, post the matching message in chat and STOP. Do NOT improvise. Do NOT fall back to general knowledge of what `<TASK_ID>` should do. Do NOT produce any widget. Do NOT continue.

For `ABORT_PLUGIN_NOT_FOUND` (no Cowork plugin clones mounted at all):

> ⚠️ Command Room scheduled task `<TASK_ID>` could not find the plugin clone in this session. No plugin is mounted under `mnt/.remote-plugins/`. Open Cowork → Customize → Personal Plugins, confirm Command Room is installed and enabled, then type `set up command room schedules`. This task will work tomorrow once the plugin is reachable.

For `ABORT_ORCHESTRATOR_NOT_FOUND` (plugin clones exist but none contain the canonical Command Room skill structure):

> ⚠️ Command Room scheduled task `<TASK_ID>` could not load its orchestrator. Plugin clones are mounted but none contain the expected Command Room skill files — usually Cowork's VHD-cache bug serving a stale snapshot. Fix: **fully quit Cowork (not just close the window — quit the app and confirm no `cowork` process remains in Task Manager / Activity Monitor), then reopen Cowork.** After restart, type `set up command room schedules` to re-register. This task will work tomorrow once the VHD remounts cleanly.

For `ABORT_WORKSPACE_NOT_FOUND`:

> ⚠️ Command Room scheduled task `<TASK_ID>` could not find your workspace. Either no folder with `_hq/data/events.jsonl` is connected to Cowork right now, or the workspace I was registered to (`<WORKSPACE_BASENAME>`) isn't connected. Please connect your Command Room workspace folder in Cowork's Settings → Folders, then type `set up command room schedules` to re-bind. This task will work tomorrow once the workspace is reachable.

## Step 2 — Verify the orchestrator content carries the canonical contract marker

Run this bash:

```bash
HEAD=$(head -c 2000 "$ORCH")
echo "$HEAD" | grep -q "OUTPUT CONTRACT" && echo "CONTRACT_OK" || echo "CONTRACT_FAIL"
```

If the output is `CONTRACT_FAIL`, post EXACTLY this message in chat and STOP. Do NOT improvise. Do NOT produce any widget.

> ⚠️ The orchestrator file for `<TASK_ID>` exists but doesn't contain the canonical OUTPUT CONTRACT marker. The plugin may be partially installed or corrupted. Please reinstall Command Room and type `set up command room schedules`.

## Step 3 — Read the orchestrator and execute it verbatim

Run this bash to fetch the full orchestrator content:

```bash
cat "$ORCH"
```

The output of that `cat` is the FULL orchestrator for this fire. Treat it as if it were prepended to your instructions: every Phase, every step, every contract clause, every leak-scanner check it specifies — execute them all, in order, without paraphrasing or skipping.

Specifically:

- The orchestrator's OUTPUT CONTRACT rules apply to your output. Honor every one.
- The orchestrator's Phase 1, Phase 2, etc. are the steps you must execute.
- Any `mcp__visualize__show_widget`, `python3 -c`, `mcp__cowork__present_files`, or other tool calls specified by the orchestrator MUST be made (you are running them, not summarizing them).
- The orchestrator's STOP CONTRACT applies after the widget posts. After the widget + Briefs/Sources sections, you stop.

Do NOT write a summary of what the orchestrator says. EXECUTE the instructions verbatim.

## Anti-improvisation contract (v2.14.24+)

You are reading a BOOTLOADER. The full orchestrator is in the file at `$ORCH`. If the bash above fails for any reason — plugin not mounted, orchestrator file missing, contract marker absent — abort per the rules in Step 1 / Step 2. Never improvise an orchestration based on the task name alone. Never produce a widget without having read the canonical orchestrator content. The bootloader's purpose is to keep the prompt always-current with the latest plugin install — silent stubs defeat that purpose. Better a clean abort than a stub fire.
