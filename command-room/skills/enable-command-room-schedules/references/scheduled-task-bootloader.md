# Bootloader template for scheduled-task registration (v2.14.24+, v2.14.26+ workspace-aware, v3.2.1+ multi-plugin-aware, **v3 merged-environment aware — BOOT3, 2026-09-19**)

This file is the canonical bootloader template registered into the scheduler at registration time. Each scheduled chat gets a copy with its placeholders substituted.

## v3 (BOOT3, 2026-09-19) — what changed and why

The merged Claude app fires a scheduled chat as a **fresh cloud session**. Three things the old bootloader relied on are not true there:

1. **No folder is attached.** The fire starts with `connectedFolders: []` even when the trigger declares its folders. What IS true (probe 5/6, 2026-09-19) is that the fire can ask for the folder itself: `device_request_folder_access` returns a grant in under two seconds when the folder is in the desktop app's Trusted folders, and otherwise opens ONE consent card on the computer that stays open about two hours. The grant is **standing per task** — answered once, never asked again. That is the new **Step 0**, and it replaces the "this run couldn't reach your computer" abort the design originally called for.
2. **`CLAUDE_PLUGIN_ROOT` is empty and `.remote-plugins` does not exist.** The plugin IS synced into the fire's container, at `/root/.claude/plugins/synced/<org>_<account>/cr/` (266 helper files, every `command-room:*` skill visible). So the plugin-root resolution grows a **third branch**, tried between the environment variable and the legacy loop.
3. **The workspace is on the customer's computer, not in the container.** Helpers live in the cloud container; the workspace is mounted only inside a sandbox VM on the PC, reached through `device_bash`. Nothing bridges them. So **Step 1.3** stops trying to `find` the workspace in the container and instead runs the workspace access layer's `discover` block on the device, verifies the runtime that was installed into the workspace, reads the brain file, and proves the layer works with one helper call before any orchestrator runs.

Everything else — the anti-improvisation contract, the root guard, the retirement gate, the contract marker, the run-mode rule, reading the orchestrator fresh — is unchanged, and the legacy path is **byte-for-byte what it was**. A seat still on the desktop app takes exactly the branches it took yesterday.

**A note on step numbering.** The design called the workspace step "Step 1.5". On this tree Step 1.5 is already the retirement gate (RETIREGATE1, pinned by name in `tests/run_taskret1_test.py`) and Step 1.4 consumes `$WORKSPACE`, so the workspace step has to sit before both. It is **Step 1.3**.

## v2.14.26 — workspace binding via baked-in basename + fallback discovery (legacy path, unchanged)

Per the diagnostic 2026-05-06: folder selection is NOT a passable parameter to the legacy registration call (silently dropped). Folder binding is implicit at fire time — the desktop app mounts whatever folders are connected when the fire runs. v2.14.26+ works around this by:

1. **At registration time** (`enable-command-room-schedules` Phase 0+1): the skill discovers the workspace, asks the customer which one to bind, then bakes that folder's BASENAME into each chat's bootloader as the `<WORKSPACE_BASENAME>` placeholder.
2. **At fire time:** Step 1.3's legacy branch first tries `WORKSPACE="$SESSION_DIR/mnt/<baked_basename>"`. If that path has `_hq/data/events.jsonl`, use it. If not (folder renamed, different folder connected), fall back to discovery — `find -name events.jsonl` (`_archive/` and `_demo-framework/` pruned — an archived or demo substrate must never win on mtime) and pick the most-recently-modified candidate. If still nothing, abort in plain English.

This handles all three legacy folder-binding semantics (snapshot-at-registration, snapshot-at-first-approval, live-at-fire) — the bootloader degrades from "explicit baked-in path" to "discover what's mounted" to "abort loudly."

## Why a bootloader, not the full orchestrator body

Pre-v2.14.24, registration pinned the full canonical orchestrator body (~366 lines) into the scheduler. That worked, but produced a class of bug: every plugin upgrade that changed orchestrator content required a re-registration, and customers who never re-ran `set up command room schedules` after upgrading kept firing stale prompts.

v2.14.24+ pins a small bootloader instead. The bootloader resolves the plugin root at fire time, reads the canonical `orchestrator-<name>.md` from the currently-installed plugin via `bash cat`, and executes it verbatim. Plugin upgrades propagate automatically. Drift is structurally impossible.

## Why `bash cat`, not the Read tool

Live test 2026-05-06 proved the Read tool from a fired session **cannot reach** the plugin clone's path — Read only sees the customer's connected folders. `bash cat` from a fired session *can*. That holds in the merged environment too: the synced plugin root is a container path, and container bash reads it directly.

So the bootloader uses `bash cat` to read the orchestrator. The output of `cat` returns as a tool result; treat it as the rest of the instructions for the fire.

## Why `ls -dt`, not `ls -d`

Plugin upgrades can leave orphaned clone directories alongside the new one. `ls -d` returns them in directory-traversal order, which can pick the OLD one. `ls -dt` sorts by mtime, picking the most recently mounted. Fixes the upgrade-window race.

## Why iterate, not `ls -dt | head -1` (v3.2.1+)

`ls -dt | head -1` picks the most-recently-mounted clone *period* — whatever plugin it is. If any other plugin was mounted more recently, the root resolves to that plugin's folder, the orchestrator file isn't there, and the abort fires. The fix is to iterate the mtime-sorted list and pick the FIRST clone that actually contains the canonical skill structure (`skills/enable-command-room-schedules/references/<ORCHESTRATOR_FILENAME>`). The same iterate-and-verify rule applies to the synced-registry branch.

## Substitution rules at registration time

The registration skill (`enable-command-room-schedules/SKILL.md` Phase 1) reads this template and substitutes:

- Every literal `<TASK_ID>` → the canonical taskId (e.g. `inbox`).
- Every literal `<ORCHESTRATOR_FILENAME>` → the orchestrator filename from `ORCHESTRATOR_MAP` (e.g. `orchestrator-inbox.md`).
- (v2.14.26+) Every literal `<WORKSPACE_BASENAME>` → the basename of the customer-confirmed workspace folder. NEVER substitute a hardcoded folder name from these docs; resolve at runtime.
- (Phase 3 / W4, 2026-07) Every literal `<PLUGIN_VERSION>` → the installed plugin version from `$PLUGIN_ROOT/.claude-plugin/plugin.json` at registration time. This stamp is DIAGNOSTIC ONLY — fire behaviour always comes from the freshly-resolved plugin, so a "stale" stamp never changes what runs. It exists so `task_watchdog.check_prompt_versions` can DETECT registered-prompt drift.
- **(v3 / BOOT3) Every literal `<WORKSPACE_ABSOLUTE_PATH>`** → the workspace folder's absolute path on the customer's computer, taken from `get_device_info`'s `connectedFolders` at registration time (or from the trigger record's `derived_state.folders[0]` when re-registering). Step 0 asks for exactly this path.
- **(v3 / BOOT3) Every literal `<CHAT_DISPLAY_NAME>`** → `schedule_config.task_display_name(<task id>)` (e.g. `Inbox`). It appears in the folder-request reason, which the customer reads on the consent card, so it must be the chat's name and never its id.
- **(IDENT1 I-9) `<WRITER_ID>` and `<WRITER_DERIVATION>`** → this registering seat's OWN writer pair, `writer_identity.bakeable_pair(<workspace path>)` — derived from the registering process's account, and baked only when any stored pair equals it (never read from the synced identity file alone) — baked by `writer_identity.bake_pair(body, writer_id, derivation)`; on a seat with no pair, `bake_pair(body)` REMOVES every line carrying either placeholder. A `<WRITER_` token never ships, and a malformed pair is refused, never exported blank.
- **(v3 / BOOT3) The literal `<DISCOVER_BLOCK>`** → the workspace access layer's discovery block, obtained at registration time by running `python3 shared/scripts/workspace_access.py discover --block` and pasting what it prints, verbatim. It is a short stdlib heredoc that finds the workspace root by anchor, reads the installed runtime's pointer file, probes the mount and prints ONE JSON line. Substituting it at registration rather than resolving a path at fire time is deliberate: the fire has no way to read the plugin's own files on the device.

The identity line `cr-task: … · cr-workspace: … · plugin-version: …` is built from the same three values and is what makes a Command Room trigger identifiable from its prompt alone.

Then the substituted body is passed to the backend's create/update plan as the prompt.

**Pulse filename alias (debugging note — docs-only; the task itself is RETIRED and the file it names is now a retirement stub):** the `pulse` task's `<ORCHESTRATOR_FILENAME>` is `orchestrator-dont-forget.md`, NOT `orchestrator-pulse.md` (which doesn't exist). That is deliberate: the filename stays for `events.jsonl` `source_skill` back-compat. Don't "fix" the mapping.

**Frontmatter rule:** the bootloader body MUST NOT start with a `---` frontmatter block. The harness prepends its own; user-supplied frontmatter creates a doubling bug (verified live 2026-05-06).

## The bootloader template (everything below this heading is the registered prompt body)

# Scheduled task bootloader — <TASK_ID>

cr-task: <TASK_ID> · cr-workspace: <WORKSPACE_BASENAME> · plugin-version: <PLUGIN_VERSION>

Registered from plugin-version: <PLUGIN_VERSION> (diagnostic stamp — the watchdog compares it against the installed plugin to detect registration drift; it never changes fire behaviour, because the orchestrator below is always read fresh from the currently-installed plugin).

You are running the scheduled chat `<TASK_ID>`. This is a bootloader, not the orchestrator. The canonical orchestrator content lives in the plugin folder and is read fresh at every fire — so plugin upgrades propagate automatically without re-registration.

## Step 0 — Make sure this run can reach the workspace folder

A scheduled run starts with no folder attached, so this step asks for one — **once, at the start**, and never again in this run except in the one case point 6 names.

1. Call `mcp__remote-devices__get_device_info`. If that tool does not exist in your tool list, this is the older desktop app: skip Step 0 entirely and go to Step 1.
2. Read `connectedFolders`. If it already contains `<WORKSPACE_ABSOLUTE_PATH>`, the folder is attached — go to Step 1 and do not request anything.
3. Otherwise call, exactly once:

   `mcp__remote-devices__device_request_folder_access(paths=["<WORKSPACE_ABSOLUTE_PATH>"], reason="Command Room's <CHAT_DISPLAY_NAME> needs its workspace folder")`

4. If the reply grants the folder, continue to Step 1. Every later run of this chat will find the folder already attached, because the grant is standing: it is answered once per chat and never asked again.
5. If the reply says nobody at the computer has answered yet, post EXACTLY this message as the whole chat turn and STOP:

   > This chat needs a one-time OK on your computer: a card is open there for about two hours; press Allow once and every later run will find your workspace.

   Then write nothing, read nothing, and **do not request the folder a second time in this run.** A second request is not a retry; it is a second card for the same person to dismiss.

6. **The one named exception: the grant is dropped LATER in this run.** Step 0 asks at the start, at most once — but a grant can disappear between calls, because the desktop app can re-prompt for a folder that was already set to always allow. You will see it as an access-layer envelope from a `device_bash` verb that had been working: `{"ok": false, "reason": "folder_not_attached"}`. When that happens, and only then, re-request the folder **exactly once** in this run:

   `mcp__remote-devices__device_request_folder_access(paths=["<WORKSPACE_ABSOLUTE_PATH>"], reason="Command Room's <CHAT_DISPLAY_NAME> needs its workspace folder")`

   Then retry the SAME command string you just ran — the same verb, the same arguments, not a substitute and not a smaller version of it. If that retry comes back `folder_not_attached` again, this run has lost the folder twice: post the message in point 5 as the whole chat turn and STOP. Write nothing, read nothing, and never ask a third time. **Two requests is the hard ceiling for one run** — the first here in Step 0, the second only ever after a dropped grant.

## Step 1 — Resolve the plugin path and the environment

Run this bash. It resolves the plugin clone (where the orchestrator file lives) and asks the plugin which environment this run is in:

```bash
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT=""
ORCH=""
# (a) Claude Code sets CLAUDE_PLUGIN_ROOT natively.
if [ -n "$CLAUDE_PLUGIN_ROOT" ] && [ -f "$CLAUDE_PLUGIN_ROOT/skills/enable-command-room-schedules/references/<ORCHESTRATOR_FILENAME>" ]; then
  PLUGIN_ROOT="$CLAUDE_PLUGIN_ROOT"
  ORCH="$PLUGIN_ROOT/skills/enable-command-room-schedules/references/<ORCHESTRATOR_FILENAME>"
fi
# (b) The merged app's account plugin registry. CLAUDE_PLUGIN_ROOT is EMPTY in a
# scheduled run, but the plugin IS synced here (verified 2026-09-19). Iterate
# mtime-sorted and take the first clone that actually carries the skill file, for
# the same reason branch (c) does.
[ -z "$PLUGIN_ROOT" ] && for d in $(ls -dt /root/.claude/plugins/synced/*/*/ 2>/dev/null); do
  candidate="${d%/}/skills/enable-command-room-schedules/references/<ORCHESTRATOR_FILENAME>"
  if [ -f "$candidate" ]; then
    PLUGIN_ROOT="${d%/}"
    ORCH="$candidate"
    break
  fi
done
# (c) The older desktop app's mounted clones.
[ -z "$PLUGIN_ROOT" ] && for d in $(ls -dt "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null); do
  candidate="${d%/}/skills/enable-command-room-schedules/references/<ORCHESTRATOR_FILENAME>"
  if [ -f "$candidate" ]; then
    PLUGIN_ROOT="${d%/}"
    ORCH="$candidate"
    break
  fi
done
echo "PLUGIN_ROOT=$PLUGIN_ROOT"
echo "ORCH=$ORCH"
# Count Command Room candidates across both shapes. Differentiates "no plugin at
# all" from "plugin present but the orchestrator file isn't where we expect".
CR_CANDIDATE_COUNT=0
for d in $(ls -dt /root/.claude/plugins/synced/*/*/ "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null); do
  [ -f "${d%/}/skills/enable-command-room-schedules/references/<ORCHESTRATOR_FILENAME>" ] && CR_CANDIDATE_COUNT=$((CR_CANDIDATE_COUNT+1))
done
ANY_PLUGIN_COUNT=$(ls -d /root/.claude/plugins/synced/*/*/ "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null | wc -l)
echo "CR_CANDIDATE_COUNT=$CR_CANDIDATE_COUNT"
echo "ANY_PLUGIN_COUNT=$ANY_PLUGIN_COUNT"
[ -n "$PLUGIN_ROOT" ] && [ -f "$ORCH" ] || {
  if [ "$ANY_PLUGIN_COUNT" -eq 0 ]; then
    echo "ABORT_PLUGIN_NOT_FOUND"
  else
    echo "ABORT_ORCHESTRATOR_NOT_FOUND"
  fi
}
# Which environment is this run in? One helper answers; anything it cannot answer
# is `unknown`, and a scheduled run refuses on unknown rather than guessing.
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell 2>/dev/null || echo CR_ENV=unknown)"
[ -z "$CR_ENV" ] && CR_ENV=unknown
export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
echo "CR_ENV=$CR_ENV"
echo "CR_ENV_SIGNALS=$(cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --signals 2>/dev/null)"
echo "CR_BRAIN_FILE=$CR_BRAIN_FILE"
[ "$CR_ENV" = "unknown" ] && echo "ABORT_ENV_UNKNOWN"
true
```

If the bash output contains `ABORT_PLUGIN_NOT_FOUND`, `ABORT_ORCHESTRATOR_NOT_FOUND` or `ABORT_ENV_UNKNOWN`, post the matching message in chat and STOP. Do NOT improvise. Do NOT fall back to general knowledge of what `<TASK_ID>` should do. Do NOT produce any widget. Do NOT continue.

For `ABORT_PLUGIN_NOT_FOUND`:

> ⚠️ Command Room's scheduled chat `<TASK_ID>` could not find the Command Room plugin in this run, so it did nothing. Open a new chat and say `set up command room schedules`; this chat will work next time once the plugin is reachable.

For `ABORT_ORCHESTRATOR_NOT_FOUND`:

> ⚠️ Command Room's scheduled chat `<TASK_ID>` found the plugin but not its instructions for this chat — usually an update still settling. Open a new chat, say `what's new in command room`, then `set up command room schedules`.

For `ABORT_ENV_UNKNOWN`:

> ⚠️ Command Room's scheduled chat `<TASK_ID>` could not tell which environment it is running in, so it did nothing. Open a new chat and say `health check`.

## Step 1.3 — Resolve the workspace

`$CR_ENV` and this run's tool list decide which of THREE shapes this is. Take exactly one branch, and take the FIRST one that matches, reading down.

**If `CR_ENV=legacy_cowork`** — the workspace is mounted in this session, exactly as it has always been. Run this bash:

```bash
WORKSPACE="$SESSION_DIR/mnt/<WORKSPACE_BASENAME>"
if [ ! -f "$WORKSPACE/_hq/data/events.jsonl" ]; then
  CANDIDATE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type f -name "events.jsonl" -path "*/_hq/data/*" -print 2>/dev/null | while read f; do echo "$(stat -c %Y "$f") $f"; done | sort -rn | head -1 | cut -d" " -f2-)
  if [ -n "$CANDIDATE" ]; then
    WORKSPACE=$(dirname "$(dirname "$(dirname "$CANDIDATE")")")
  else
    WORKSPACE=""
  fi
fi
export CR_WORKSPACE="$WORKSPACE"
export CR_FIRED_VIA=scheduled
export CR_WRITER_ID=<WRITER_ID>
export CR_WRITER_DERIVATION=<WRITER_DERIVATION>
echo "WORKSPACE=$WORKSPACE"
echo "CR_WORKSPACE=$CR_WORKSPACE"
[ -n "$WORKSPACE" ] && [ -f "$WORKSPACE/_hq/data/events.jsonl" ] || echo "ABORT_WORKSPACE_NOT_FOUND"
```

**If `CR_ENV=merged_cloud` and this run carries no `mcp__remote-devices__device_bash`** — the task's declared folder is **mounted into this container**, and there is no device shell to reach it through anyway. That is what a scheduled fire on the merged app actually gets (re-walk 2026-09-21, ruling R-RW-2): the container mounts the granted folder under this session's own mount point exactly the way the legacy sandbox did, and every helper runs beside the data, in this shell.

The mount is the condition, so prove it rather than assume it: the block below looks for the ledger at the baked-in basename under the session mount and falls back to discovery, and prints `ABORT_WORKSPACE_NOT_FOUND` when neither finds one — which is the answer that sends this fire to the branch below instead. Resolve the workspace with the SAME bash the legacy branch above runs — the identical block, because it is the identical question —

```bash
WORKSPACE="$SESSION_DIR/mnt/<WORKSPACE_BASENAME>"
if [ ! -f "$WORKSPACE/_hq/data/events.jsonl" ]; then
  CANDIDATE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type f -name "events.jsonl" -path "*/_hq/data/*" -print 2>/dev/null | while read f; do echo "$(stat -c %Y "$f") $f"; done | sort -rn | head -1 | cut -d" " -f2-)
  if [ -n "$CANDIDATE" ]; then
    WORKSPACE=$(dirname "$(dirname "$(dirname "$CANDIDATE")")")
  else
    WORKSPACE=""
  fi
fi
export CR_WORKSPACE="$WORKSPACE"
export CR_FIRED_VIA=scheduled
export CR_WRITER_ID=<WRITER_ID>
export CR_WRITER_DERIVATION=<WRITER_DERIVATION>
echo "WORKSPACE=$WORKSPACE"
echo "CR_WORKSPACE=$CR_WORKSPACE"
[ -n "$WORKSPACE" ] && [ -f "$WORKSPACE/_hq/data/events.jsonl" ] || echo "ABORT_WORKSPACE_NOT_FOUND"
```

— and then take points 2, 3, 4 and 5 of the merged branch below **in this shell**, with no `device_bash` anywhere: `expect` against the installed runtime (the STOP on a mismatch stands exactly as written), the brain file through the access layer's `read` verb, the proof through the `plan run_helper` form, and the exports of point 3. `CR_FIRED_VIA=scheduled` is already exported by the block above; add `export CR_DEVICE_WORKSPACE="<WORKSPACE_ABSOLUTE_PATH>"` before the first `plan` so a landed document can name its path on the customer's own computer.

**If `CR_ENV=merged_cloud` and the run DOES carry `mcp__remote-devices__device_bash`** — the workspace is NOT in this container. It is on the customer's computer, inside the sandbox VM, and every read of it goes through that tool. **Every access-layer line on this branch is RENDERED IN THE CONTAINER** — `cd "$PLUGIN_ROOT" && CR_FIRED_VIA=scheduled CR_DEVICE_WORKSPACE="<WORKSPACE_ABSOLUTE_PATH>" python3 shared/scripts/workspace_access.py plan <verb> --json '…'` — and the ONE line it prints is pasted into `device_bash` unchanged. The two variables in front of `python3` are this body's own facts, substituted at registration, so the line carries the writer identity even in a shell where no earlier export survived (the account is this container's own; nothing reads a synced file). Never run `plan` from the staged runtime through `device_bash`: the account this chat runs under lives in this container, so a line rendered on the device carries no writer identity, and a write it names is refused (IDENT1 I-11). Take these five steps in order:

1. **Discover.** Run this block through `mcp__remote-devices__device_bash`. It is the access layer's own `discover` block, substituted at registration, and it prints ONE JSON line describing the workspace, the runtime installed in it and what the mount allows:

   <DISCOVER_BLOCK>

2. **Check the runtime.** The question is whether the runtime installed in the workspace matches the plugin THIS FIRE resolved in Step 1 — never this prompt's `plugin-version` stamp, which is diagnostic and is deliberately never rewritten on a version-only bump, so comparing against it would stop every chat on the first patch release after registration.

   Ask the resolved plugin what it expects, in this container:

   ```bash
   EXPECT=$(cd "$PLUGIN_ROOT" && python3 shared/scripts/workspace_access.py expect)
   echo "$EXPECT"
   ```

   It prints one JSON line carrying `plugin_version` and `manifest_sha`. Compare it against the discovery JSON from point 1:

   - `runtime_present: false` → the runtime is not installed. Post the message below and STOP.
   - the discovery JSON's `manifest_sha` is null, or differs from the `manifest_sha` that `expect` printed → the installed runtime is not this plugin's. Post the message below and STOP.
   - otherwise the runtime matches the resolved plugin — continue, whatever this prompt's `plugin-version` stamp says. A stamp older than `expect`'s `plugin_version` is registration drift for the watchdog to report, not a reason to stop this fire.

   If `expect` cannot be run at all (no output, or output that is not JSON), continue: the runtime was found and probed in point 1, and point 5 proves it actually works. Stopping a fire on a failed self-check the customer cannot clear is the failure this step exists to avoid.

   The message, posted EXACTLY as the whole chat turn:

   > Command Room's runtime is not installed in your workspace yet, so this chat did nothing. Open a new chat and say `what's new in command room`.

3. **Export, straight after the check — before ANY access-layer line is rendered (INBOXDRIVE1; the re-walk's first two pastes named an undefined runtime variable because these exports came two points later).** Export the name, under BOTH names, and the two facts only this body knows: `export CR_WORKSPACE=<the JSON's vm_path>` and `export WORKSPACE="$CR_WORKSPACE"`, for the container-side steps that only need to NAME the workspace. Then `export CR_STAGED_ROOT=<the JSON's staged_root>` — the staged runtime's directory on the customer's computer, which every `plan` rendered here names in front of `workspace_access.py` (a container has no mount to find it in; without this export a rendered line would name the in-shell runtime variable, which a fresh device shell does not define). Then `export CR_FIRED_VIA=scheduled` and `export CR_DEVICE_WORKSPACE="<WORKSPACE_ABSOLUTE_PATH>"` (the path substituted into this file at registration — where the folder lives on the customer's own computer). **This body IS the scheduled fire**, so it is the one process that can say so without guessing, and the one that already holds the customer-side path; `workspace_access.plan` puts both in front of every command it renders, so a helper running beside the data reads them instead of being told by a model. A hand-triggered replay of this same body is still a scheduled-CONTEXT run; how far off-slot it landed is what `slot_provenance` says on the receipt. Nothing an interactive chat runs sets either variable, which is why a typed brief lands `manual`. Nothing container-side opens it: the path is on the other machine. Setting `WORKSPACE` too is what keeps the later steps honest — Step 2.5 passes `CR_WORKSPACE="$WORKSPACE"` into its subprocesses, and on this branch `$WORKSPACE` would otherwise be empty, so those steps would name no workspace at all. Every later access-layer line in this fire is rendered the same way — `cd "$PLUGIN_ROOT" && CR_FIRED_VIA=scheduled CR_DEVICE_WORKSPACE="<WORKSPACE_ABSOLUTE_PATH>" python3 shared/scripts/workspace_access.py plan <verb> --json '…'` in the container, the printed line into `device_bash` — so these exports ride on it. The writer this chat was registered under, exported beside them:
   `export CR_WRITER_ID=<WRITER_ID>`
   `export CR_WRITER_DERIVATION=<WRITER_DERIVATION>`

4. **Read the brain file.** `CR_BRAIN_FILE` from Step 1 is EMPTY on this shape by design — nothing here stages a brain file; the path is discovery's `brain_file`, and that is what this point reads. When the JSON's `brain_file` is not null, read it first, through the access layer's `read` verb — rendered in the container, `cd "$PLUGIN_ROOT" && CR_FIRED_VIA=scheduled CR_DEVICE_WORKSPACE="<WORKSPACE_ABSOLUTE_PATH>" python3 shared/scripts/workspace_access.py plan read --json '{"rel": "CLAUDE.md"}'`, then the printed line through `device_bash` — before anything else in this fire. It carries how this workspace's owner wants to be spoken to, and a fire that skips it speaks in a default voice the customer never chose.

5. **Prove the layer works.** Run exactly one helper through the access layer and check that its envelope comes back `ok: true`:

   `cd "$PLUGIN_ROOT" && CR_FIRED_VIA=scheduled CR_DEVICE_WORKSPACE="<WORKSPACE_ABSOLUTE_PATH>" python3 shared/scripts/workspace_access.py plan run_helper --json '{"name": "events_io:count_rows", "args": {"root": "<vm_path>"}}'`

   rendered HERE, in the container, and the ONE line it prints — `CR_PLAN_ORIGIN=container` and the writer identity in front of `python3 "<staged_root>/…"` — pasted into `device_bash` unchanged. The argument is the WORKSPACE ROOT (`<vm_path>` = the discovery JSON's `ws`) — the helper resolves the ledger itself, and the access layer binds these arguments by NAME onto the helper's own parameters, so a key the helper does not take comes back `ok: false` and this chat stops for nothing. `events_io.count_rows` counts the rows in the ledger — the cheapest possible read that proves helpers, workspace and mount are all reachable together. If the envelope is `ok: false`, treat it exactly like a missing runtime and post the message in point 2. If it says `folder_not_attached`, that is Step 0 point 6: re-request the folder exactly once, retry the SAME command string, and stop on a second drop.

If either branch prints `ABORT_WORKSPACE_NOT_FOUND`, post this message in chat and STOP:

> ⚠️ Command Room's scheduled chat `<TASK_ID>` could not reach your workspace folder from this run, so it did nothing. Open a new chat with your Command Room folder attached and say `set up command room schedules` to re-bind.

## Step 1.4 — Fire-time root guard (SPEC PATHREPAIR1)

Step 1.3 found a LIVE `$WORKSPACE` — but that is not the same question as whether this workspace's OWN registration record (`_hq/workspace_config.json`'s `workspace_root`) still agrees with it. A folder rename or move leaves the stored value stale even on a fire that resolved fine: this step corrects that stale self-reference against the now-confirmed live `$WORKSPACE`, or aborts loudly rather than letting a chat keep running on a registration nobody can trust.

Ask the guard through the access layer — ONE rendered `run_helper` form, the same door every other read of the workspace goes through:

`cd "$PLUGIN_ROOT" && CR_FIRED_VIA=scheduled CR_DEVICE_WORKSPACE="<WORKSPACE_ABSOLUTE_PATH>" python3 shared/scripts/workspace_access.py plan run_helper --json '{"name": "fire_guards:root_guard", "args": {"root": "<vm_path>"}}'`

(rendered HERE, in the container, like every access-layer line in this file — then the ONE line it prints runs in this shell on the legacy and mounted shapes and is pasted into `device_bash` unchanged on the device shape. The same holds for every fire question below. A line rendered on the device carries no identity, so the scheduled-writer question would answer `foreign: true` for this workspace's own chat.)

**The envelope IS the answer.** Its `result` carries `blocked`, `state` and `detail`; nothing parses it a second time. `result.blocked: true` → post the message below as the whole turn and STOP. Anything else → continue to Step 1.5.

**`ok: false` is the FALL-THROUGH, never a short-circuit** — the same posture Step 1.5 takes below. If the helper cannot be reached at all, or the envelope comes back `ok: false` for any reason, treat it as `blocked: false` and let the fire proceed unchanged. This is the ONE place in this file where an `ok: false` is not a stop, and the reason is the one this step has always given: a live chat must never be silenced by a hiccup in a guard that was only ever a belt. The cost of guessing wrong in that direction is one unchecked fire; the cost of guessing wrong in the other is a working chat that goes quiet and never says why.

If `result.blocked` is true, post EXACTLY this message in chat and STOP (same discipline as the ABORT messages above — do not improvise, do not produce a widget, do not continue):

> ⚠️ Command Room scheduled task `<TASK_ID>` can't confirm your workspace registration. Your workspace folder doesn't match what's on record — it may have moved or been renamed, and I found more than one folder (or none) that could be it, so I won't guess. Please open Command Room and say "set up command room schedules" to reconnect it. This task will work again once the registration is confirmed.

If `result.blocked` is false, continue to Step 1.5 unchanged. A `state` of `"STALE"` is silent here by design: the stored root is out of date and a repair WOULD succeed, but a fire does not repair the registration underneath itself — the health check and the weekly cleanup own that, and this fire has already proved it is running against a live workspace. Same silence when `state` is `"UNKNOWN"` (SPEC PATHREPAIR1 v2, ruling 1/2/3) — the fire is running inside a session mount, or this workspace's registration belongs to a different machine's own `machines` entry: neither is evidence the workspace moved, so nothing repairs, nothing alarms, and BLOCKED stays `0` exactly like a healthy root. This is the fix for the class of bug this guard used to CAUSE: a v1 build compared the stored root against `$WORKSPACE` by plain existence, which reads DEAD on every single cloud fire (the session mount never matches a real machine path) and would self-heal by writing the ephemeral `/sessions/...` path into `workspace_config.json` every time — the exact defect WALKFIX1 exists to purge.

## Step 1.5 — Is this task RETIRED? (SPEC RETIREGATE1)

A retired chat must say that it retired. It must never say the plugin is broken — and before this step existed it said exactly that, because Step 2's contract grep runs BEFORE Step 3 ever opens the file. A retirement stub that had not been given the `OUTPUT CONTRACT` marker failed that grep, the fire aborted with "the plugin may be partially installed or corrupted", and the customer's only available workaround was to switch the task off themselves (BUG-9517). This step makes the corruption message unreachable for a retired task id.

Ask the registry through the same door — ONE rendered `run_helper` form:

`cd "$PLUGIN_ROOT" && CR_FIRED_VIA=scheduled CR_DEVICE_WORKSPACE="<WORKSPACE_ABSOLUTE_PATH>" python3 shared/scripts/workspace_access.py plan run_helper --json '{"name": "fire_guards:retirement", "args": {"task_id": "<TASK_ID>"}}'`

Membership AND class come from the registry, never from a name anyone remembers. A RENAMED row is deliberately NOT retired: its orchestrator is the live successor pack, so that chat keeps firing the real surface forever. Every other retired class — eliminated, readiness, and any class the registry grows later — answers `retired: true`, which is the safe direction, because the file such an id resolves to is a stub that can only post one line anyway.

**The envelope IS the answer.** If `result.retired` is true: post `result.line` in chat, exactly as it came back, as the ENTIRE chat turn — then STOP. **Skip Step 2 and Step 3 completely.** No widget, no scans, no connector reads, no substrate writes of any kind — not even a receipt; a retired chat that keeps writing receipts is a retired chat that still looks alive. Do NOT offer to re-register the task, do NOT propose an alternative schedule, and do NOT run any part of the old prompt "just this once."

**Do not append a deregistration suggestion of your own.** The line already carries the right one for its class, and the classes disagree on purpose: an ELIMINATED chat's line offers the `pause` that clears it, while a READINESS retirement's line deliberately offers none — the update that retired it already switched the task off, and asking the customer to perform a tap the product has already taken reads as the product not knowing its own state. `schedule_config.retirement_line` is the ONE renderer for this sentence; whatever it printed is the whole message.

Then ask ONE more question through the same door — is THIS chat the workspace's declared scheduled writer (SAFETY0, one scheduled writer per workspace)?

`cd "$PLUGIN_ROOT" && CR_FIRED_VIA=scheduled CR_DEVICE_WORKSPACE="<WORKSPACE_ABSOLUTE_PATH>" python3 shared/scripts/workspace_access.py plan run_helper --json '{"name": "fire_guards:scheduled_writer", "args": {"root": "<vm_path>"}}'`

If `result.foreign` is true: post `result.line`, exactly as it came back, as the ENTIRE chat turn — then STOP. No write of any kind, no widget, no receipt. `ok: false` here is the same fall-through as the retirement question (R-FIX3-2's exception): proceed.

If `result.retired` is false, continue to Step 2 unchanged. **`ok: false` is the FALL-THROUGH, never a short-circuit** — the second and last place in this file where that is true: if the helper cannot be reached or the registry cannot be read, a live task must still fire, and the cost of guessing wrong in that direction is one ordinary fire rather than a working chat silenced without being asked. That is also why this step is a belt and not the only brace — every retirement stub carries the `OUTPUT CONTRACT` marker inside its own first 2000 bytes, so Step 2 passes and Step 3 reaches a file that posts the same line by itself. **The same line, byte for byte:** every stub's quoted line is pinned equal to `schedule_config.retirement_line(<its id>)`, which is the sentence this step prints, so the two paths cannot answer one customer two ways. Either half alone produces the retirement line; the pair is what makes the corruption message unreachable.

## Step 2 — Verify the orchestrator content carries the canonical contract marker

Run this bash:

```bash
HEAD=$(head -c 2000 "$ORCH")
echo "$HEAD" | grep -q "OUTPUT CONTRACT" && echo "CONTRACT_OK" || echo "CONTRACT_FAIL"
```

If the output is `CONTRACT_FAIL`, post EXACTLY this message in chat and STOP. Do NOT improvise. Do NOT produce any widget.

> ⚠️ The orchestrator file for `<TASK_ID>` exists but doesn't contain the canonical OUTPUT CONTRACT marker. The plugin may be partially installed or corrupted. Please reinstall Command Room and type `set up command room schedules`.

## Step 2.4 — Is this chain migrated? (IDENT1 I-15)

After `CONTRACT_OK`, ask once whether this chat's chain has moved onto the workspace access layer — rendered in the container like every access-layer line (the census it reads is the plugin's own):

`cd "$PLUGIN_ROOT" && CR_FIRED_VIA=scheduled CR_DEVICE_WORKSPACE="<WORKSPACE_ABSOLUTE_PATH>" python3 shared/scripts/workspace_access.py plan run_helper --json '{"name": "migration_gate:gate", "args": {"env_mode": "<CR_ENV>", "orchestrator_rel": "skills/enable-command-room-schedules/references/<ORCHESTRATOR_FILENAME>", "task_id": "<TASK_ID>"}}'`

If `result.stop` is true: post `result.line`, exactly as it came back, as the ENTIRE chat turn — then STOP. Write NOTHING: no receipt, no page, no push beyond that same line. Anything else (including `ok: false`) → continue to Step 2.5 unchanged; a legacy or local seat never stops here.

## Step 2.5 — Determine the run mode (DOGFIX1 2026-07-27)

**This prompt is not evidence of the run mode.** One body is registered per chat and replayed for the scheduled fire AND for any hand-started re-run, so "scheduled chat `<TASK_ID>`" above names WHICH chat you are running, never HOW this fire started. Decide once, here, and carry the answer into the orchestrator's Phase 2.9 `fired_via`:

- **`manual`** — a human-authored message exists anywhere in this session, or a human asked for a re-run, **or you cannot tell**. A re-run started by hand carries the text `Command Room manual re-run — <timestamp>`; that text is proof of `manual`.
- **`scheduled`** — the scheduler started this session with no human message initiating the turn, and you are sure.

**When uncertain, it is `manual`** (`shared/RECEIPT_CONTRACT.md` § Run-mode detection). A mis-labeled manual costs one missing lateness note; a mis-labeled scheduled refuses a surface a human asked for. Pass the literal word `scheduled` or `manual` — never the placeholder `<scheduled|manual>`, never a description. Say the word.

**Every helper call in this fire carries `CR_WORKSPACE` and `CR_ENV` (CLOCK1 + BOOT3).** Step 1 and Step 1.3 exported them, but each helper call you run is a separate process and shell state does not always survive between tool calls. So prefix the rendered command: `CR_ENV="$CR_ENV" CR_WORKSPACE="$WORKSPACE" <the command the access layer rendered>`. The same prefix is right on every branch: on the two merged ones, Step 1.3 set `WORKSPACE` from `CR_WORKSPACE` for exactly this reason. This is not decoration. The phases that gather context run BEFORE the lateness check and write to the ledger from those subprocesses; without the variables they cannot find the workspace, cannot cross-check the clock, and stamp whatever the machine says — which on the machine this was built for was two days wrong.

**Pass the session date as well.** Phase 2.9 also takes `env_date` — this session's own date, the `Today's date is YYYY-MM-DD` line in your context. It is how the fire cross-checks this computer's clock against something other than itself. If `check_lateness` comes back with a **clock notice**, that notice is the FIRST line you post, above everything else including the lateness banner: the dates in the surface came from the workspace record rather than the machine, and the reader has no other way to know that.

## Step 3 — Read the orchestrator and execute it verbatim

Run this bash to fetch the full orchestrator content:

```bash
cat "$ORCH"
```

The output of that `cat` is the FULL orchestrator for this fire. Treat it as if it were prepended to your instructions: every Phase, every step, every contract clause, every leak-scanner check it specifies — execute them all, in order, without paraphrasing or skipping.

Specifically:

- The orchestrator's OUTPUT CONTRACT rules apply to your output. Honor every one.
- The orchestrator's Phase 1, Phase 2, etc. are the steps you must execute.
- Any `mcp__visualize__show_widget`, any rendered access-layer command, the harness's file card when it offers one, or other tool calls specified by the orchestrator MUST be made (you are running them, not summarizing them).
- The orchestrator's STOP CONTRACT applies after the widget posts. After the widget + Briefs/Sources sections, you stop.
- When this run's tool list has NO widget tool after the load in `shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first (a listed deferred tool counts as present: it is loaded, never read as absent), the orchestrator's text fallback IS the surface (`inbox_helpers.plan_delivery`: the list, the saved page, the counts-only push) — never a stop message, never a sentence about the missing tool.

Do NOT write a summary of what the orchestrator says. EXECUTE the instructions verbatim.

## Step 3.1 — FINAL RESPONSE (R-RW3-4)

This run's final response is what the phone shows for the task. When the orchestrator's one-command driver answers with a `text` (the inbox chat after its mail fetch), the last message of this run is exactly the driver's `text`, unchanged; when `SendUserMessage` exists it is called with the same text first; nothing is written before the first line of that text or after its last line — no summary of what ran, no note about tools, no file name, no variable, no apology. A refusal's `lines` are the whole final response the same way. Every other chat: the orchestrator's own surface — its widget and sections, its one composed line, or nothing on a silent fire — is the whole final response, and nothing follows it.

## Anti-improvisation contract (v2.14.24+)

You are reading a BOOTLOADER. The full orchestrator is in the file at `$ORCH`. If the bash above fails for any reason — plugin not found, orchestrator file missing, environment unknown, workspace unreachable, contract marker absent — abort per the rules in Step 1 / Step 1.3 / Step 2. Never improvise an orchestration based on the chat name alone. Never produce a widget without having read the canonical orchestrator content. The bootloader's purpose is to keep the prompt always-current with the latest plugin install — silent stubs defeat that purpose. Better a clean abort than a stub fire.
