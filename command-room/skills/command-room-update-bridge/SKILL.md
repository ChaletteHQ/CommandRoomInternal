---
name: command-room-update-bridge
description: "Applies product updates to this workspace: default dashboards, release notes, and pending workspace-file migrations — additively, archive-never-delete. Fires on: 'what's new in command room', 'whats new', 'update command room', 'check for updates', 'install my dashboards', 'install missing dashboards', 'install the latest'. Idempotent — re-runs are safe; detects which defaults are already installed and which release manifests haven't been applied, announces changes in plain English, and instructs the one-time actions (like re-registering schedules) when a release needs them. Does NOT fire on 'level up command room' (level-up-command-room — opt-in add-ons menu), 'rebuild [artifact]' (the owning enable-* skill), or 'restart onboarding' (command-room-onboarding). Migration mechanics and manifest contract: Routing section in the body."
---

# Command Room Update Bridge — v3.13.0+ (Option B canonical-edit-surface migrations added)

## What this skill does

This skill is the **canonical install path for the two Layer 1 default sidebar dashboards** (Orgs Map, Quick Commands). It serves two user populations with one flow:

1. **Fresh post-onboarding users.** `command-room-onboarding` M1 (2026-05-23+) installs the Workspace Map directly in Phase 1b (customer types `install workspace map` in Chat 3) and Quick Commands silently in Phase 1a. This skill is the fallback for any default the customer didn't end up installing during M1 — and it remains the canonical `install my dashboards` re-install / repair path any time post-onboarding.
2. **Upgrade users.** Their `command-room-onboarding` checkpoint is `status: "complete"` from a prior version, so the existing-workspace guard at Phase 0a stops onboarding from re-running. This skill detects which subset of the current defaults are already installed and installs only the missing ones, surfacing what changed in the version diff. Users coming from pre-v2.9.0 versions may have retired artifacts (Daily Command Center, People Network, Commitments Tracker, Process Meetings, Daily Today) pinned — those keep working but are no longer auto-refreshed; surface a one-line nudge to unpin them.

Similarly, **workspace-level migrations** (e.g., the v2.7.9 prompt-restructuring Preferences entry in CLAUDE.md) won't apply automatically — those edits live in the user's workspace folder, not in the plugin source, so a plugin update on its own can't reach them.

This skill is the bridge for both. It:

1. **Detects which current default artifacts are missing** by reading `_hq/data/events.jsonl` for `artifact_installed` events
2. **Detects which workspace-level migrations are pending** (CLAUDE.md preference additions, BUSINESS_CONTEXT additions, etc.) via marker checks against the user's actual workspace files
3. **Surfaces what's new** in plain language ("here's what your version is missing")
4. **Applies missing defaults + workspace migrations** with a single user confirmation (calibration question per migration where applicable)
5. **Logs a `plugin_update` event** so this skill is idempotent on re-runs

It is **conservative by design.** It does not modify existing data, does not run schema migrations (no schema changed in v2.7.9), and does not force install or force apply. The user always sees the list of what will change before committing, and workspace-migration items that need calibration always ask the calibration question rather than guessing. The one automatic, non-confirmation exception is the substrate-corruption self-heal (Phase 4.4) — it is purely protective (quarantine, never delete), idempotent, and surfaces a friendly note only when it actually repaired something.

**Skill behavioral updates** (e.g., the entity-aware intel-intake in v2.7.9) are NOT handled by this skill — those apply automatically when the plugin SKILL.md files update through Anthropic's standard plugin distribution. This skill only handles the gaps that distribution can't bridge: artifact installations and workspace-folder file edits.

---

## Critical Behavioral Rules

1. **Show before do.** Never auto-install. Always list what's missing first, get one confirmation, then install.
2. **Don't deprecate user choice.** If the user has Command Atlas, Commitment Cockpit, or Pay Attention To pinned (artifacts retired in v2.7.9 / v3.11.0 from the defaults), do NOT auto-uninstall them. They're harmless. Optionally surface them as "from an older version and no longer updated — say 'unpin [name]' if you don't want them."
3. **Idempotent.** Re-running this skill after a successful update detects the prior `plugin_update` event and either says "you're up to date" or surfaces the next pending update. Migrations the user explicitly declined are NOT re-prompted on subsequent runs (a `workspace_migration_skipped` event prevents the loop).
4. **Single confirmation for the install batch, calibration questions for migrations.** The artifact install + migration list goes through one parent confirmation. Migrations that need calibration (Yes/Sometimes/No questions) ask their own question after the parent confirmation — not a duplicate "are you sure?" prompt, but the actual calibration content.
5. **Cowork-only for the artifact installs.** Mirror the same graceful degradation as `command-room-onboarding` Phase 1a's silent Quick Commands install: if `mcp__cowork__create_artifact` is unavailable, surface the chat-based fallback and skip artifact installs. Workspace migrations work fine without Cowork — keep applying them.
6. **Surgical edits only on workspace files.** Phase 4.5 migrations append to existing sections; never regenerate, never reorder, never touch unrelated sections. If the target structure is missing (e.g., no `## Preferences` heading in CLAUDE.md), skip with a clear message — don't try to recover.
7. **NEVER improvise an artifact. (Added v2.7.10 — hotfix.)** The canonical templates shipped WITH this plugin at `skills/enable-workspace-map/references/orgs-map-artifact.html` and `skills/enable-quick-commands/references/quick-commands-artifact.html` are the **only** source. If `mcp__cowork__create_artifact` fails for any reason — payload size limit, tool unavailable, tool error, truncated return, encoding mismatch — DO NOT generate a substitute. DO NOT hand-roll a "compact equivalent." DO NOT inline a different HTML page. DO NOT compress, summarize, or simplify the template on the fly. Surface the failure verbatim to the user, log `artifact_install_failed` with the exact error, and STOP. Hand-rolled substitutes shipped three independent bugs to a real client install on 2026-04-26 (see references/HISTORY.md). Improvising is now a forbidden behavior, not a fallback. If the canonical template is genuinely too large for the tool, that is a packaging problem to fix upstream in the plugin source repo, not a problem to route around in this skill.
8. **Verify every artifact install before logging success. (Added v2.7.10 — hotfix.)** After `create_artifact` returns, sanity-check the install before writing the `artifact_installed` event:
   - **Size check:** Read back the installed artifact byte length. It must be ≥ 80% of the source template's byte length on disk. (Orgs Map source ≈ 30 KB → installed ≥ 24 KB. Quick Commands source size varies → installed ≥ 80% of source.) Anything smaller is a stub or truncation.
   - **Marker check:** Confirm the installed artifact contains a known marker string from the source template — for Orgs Map, the literal `data-artifact="orgs-map"`; for Quick Commands, the literal `data-artifact="quick-commands"`. (If a template doesn't yet have a marker, add one in the plugin source repo first; do not skip the check.)
   - **Encoding check:** Confirm the installed artifact does NOT contain the byte sequence `â€` (the UTF-8-as-Latin-1 mojibake signature). If it does, the encoding round-tripped wrong — treat as a failed install.
   - If any check fails: log `artifact_install_failed` with `{artifact, reason: "verification_failed:<which>", error_text}`, surface to user, STOP. Do NOT log `artifact_installed`. Do NOT continue to the next artifact in the batch — the same failure mode likely affects it.
9. **Output guard — internal tokens, paths, and event names only (re-scoped v4.6.1 S3, F-06/F-07):** no file paths, no event-type names, no internal tokens (Rule numbers, schema fields, skill mechanics) in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary. Version numbers are NOT internal tokens — the version diff and per-version recap lines are explicitly allowed (Rule 10 owns that surface; pre-4.6.1 this rule also banned version numbers, which contradicted Rule 10's mandated one-liner — F-06. Rule 10 wins).
   - BAD: "Artifact installed. Rule 8 verified the renderer output (source bytes) — the bridge cannot read the installed file directly to auto-verify."
   - GOOD: "✓ Installed. Pin it in your sidebar, then open it once and confirm it looks right — I can't see the installed copy from here, so you're the final check."
10. **What-changed content comes from the release manifests, NEVER from CHANGELOG.md. (Added v2.7.24; amended v4.6.1 S3 per F-07 P2b.)** CHANGELOG.md is dev-internal — file paths, Rule numbers, version-by-version implementation detail — and stays forbidden verbatim OR paraphrased. What the bridge DOES say: the version diff in one line — phrased as currency, not identity: "your workspace was last brought current at v[INSTALLED]; the plugin is now v[CURRENT]" (F-07 P2d: "you're on 4.2.0" right after the user clicked Update reads as a contradiction) — followed by a plain-English recap of what's new, sourced ONLY from the release manifests' announce items (`shared/releases/v*.json` prompt/notice templates — already written for the customer; the dogfood confirmed the recap is the right product answer, F-07 P2b), then the artifact + migration lists and the confirmation ask. If the user asks for MORE detail than the manifests carry, link the GitHub CHANGELOG — still never paraphrase it inline.

---

## Phase 1: Detect plugin version + installed artifacts

Read three things:

1. **Current plugin version** from `$PLUGIN_ROOT/.claude-plugin/plugin.json` → `version` field (e.g., `"2.7.8"`). Resolve `$PLUGIN_ROOT` via the canonical CONTRACT.md Rule 22 discovery preamble at the start of every multi-step bash invocation: `SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)`.

2. **Last installed version** from the most recent `plugin_update` event in `_hq/data/events.jsonl`. If none exists, infer from the most recent `onboarding_checkpoint` event with `status: "complete"` — its `last_writer` carries the version that ran onboarding (e.g., `"command-room-onboarding"` from v2.7.4 ran a pre-checkpoint version of the build phase). If neither exists, treat installed version as "unknown legacy" and assume all v2.7.9 defaults are missing.

3. **Installed artifact set.** Scan `events.jsonl` for `artifact_installed` events and collect their `artifact` field values. Build a Set. Current-version installs will have `{orgs-map, quick-commands}`. Legacy installs may have any of the RETIRED ids listed below — those keep working if pinned but are no longer auto-refreshed by this bridge.

   **Liveness re-verification (v3.18.4+, Bug #88 — verify LIVE, don't trust the event marker alone).** An `artifact_installed` event is a *hint*, not proof the artifact is currently live in the sidebar — a user can remove an artifact, or an install can log its event but not persist (the v3.18.1 incident — see references/HISTORY.md § Bug #88). So, before treating anything as installed, **reconcile the event-derived set against the live sidebar via `mcp__cowork__list_artifacts`** (the same tool Phase 4 uses to decide create-vs-update — we can't read artifact *bytes* without `read_artifact_bytes`, but we CAN confirm *presence* by id). An artifact counts as installed ONLY if its id appears in `list_artifacts`. **Drop from `installed_artifact_set` any id that has an `artifact_installed` event but is absent from the live list** — that stale marker falls into `missing_defaults` and gets reinstalled (idempotent + Rule 8-verified, so a redundant reinstall is harmless). If `list_artifacts` is unavailable (non-Cowork session or tool error), fall back to the event-derived set but say so plainly — *"couldn't confirm your live sidebar state"* — rather than asserting "already installed." Never report "already installed" from the event log alone.

The **current Layer 1 default artifact set** (v2.9.0 architectural reset) is:

```
CURRENT_DEFAULTS = {
  orgs-map,                 // enable-workspace-map (simplified nav tree + Refresh + Run cleanup buttons; skill renamed v3.5.0, artifact id preserved)
  quick-commands            // enable-quick-commands (curated 19-command cheat sheet, block-grid)
}
```

Just two artifacts. v2.9.0 retired Daily Today, Process Meetings, People Network, and Commitments Tracker — their content moved into 6 persistent scheduled chats (Meetings Today, Inbox Pulse, Commitments You Owe, Commitments Owed To You, Cracks Watch, Meetings Processed). The chat-first architecture replaces dashboard-browsing with action delivery on a topic-by-topic basis. Memory substrate (entities.json / events.jsonl / aliases.json / voice samples) unchanged — those are the moat, not the dashboards.

These two are what the bridge installs in Phase 4. After artifact install, Phase 4.7 fires `enable-command-room-schedules` to register the M1 first-install scheduled-task set (Morning Brief, Upcoming Meetings, Past Meetings, Inbox, Friday Wrap — 5 tasks; the remaining 2 defaults Commitments and Pulse stay deferred to a follow-up session).

The current optional add-on set (NOT installed by this skill — surfaced as "available" via `level up command room`):

```
ADDONS = {
  // empty as of v3.11.0 — commitment_cockpit retired (moved to RETIRED below)
  //                       and folded into the daily Commitments scheduled chat
}
```

**Retired Layer 1 ids** — older versions installed these. They keep working if pinned (the bridge does NOT auto-uninstall) but are subsumed by the two `CURRENT_DEFAULTS` above:

```
RETIRED = {
  workspace_map,            // pre-v2.7.14 unified Workspace Map
  workspace-map-v2,         // v2.7.13 confabulation id
  project_pulse,            // pre-v2.7.9
  people_radar,             // pre-v2.7.9
  daily-command-center,     // v2.7.9-v2.7.25
  daily_command_center,     // legacy underscore variant
  daily-meetings,           // v2.7.26 only
  process-meetings,         // v2.7.27-v2.8.x — content moved to cr-meetings-processed scheduled chat in v2.9.0
  daily-today,              // v2.7.26-v2.8.x — content moved to cr-meetings-today + cr-cracks-watch chats in v2.9.0
  people-network,           // v2.7.14-v2.8.x — content moved to cr-cracks-watch chat in v2.9.0
  commitments-tracker,      // v2.7.14-v2.8.x — content moved to cr-commitment-nudge + cr-commitment-chase chats in v2.9.0
  commitment_cockpit        // v2.x-v3.10.x — content folded into the daily `commitments` scheduled chat in v3.11.0
}
```

After successful install of the two `CURRENT_DEFAULTS`, surface the architectural-shift nudge if any `RETIRED` ids are present in `installed_artifact_set`. **Render the chat names and count from the registry at fire time** — list what is actually registered for THIS workspace (`mcp__scheduled-tasks__list_scheduled_tasks` reconciled against `ORCHESTRATOR_MAP`, display names via `task_display_name()`), never a hardcoded list. No version numbers, no release history. Shape (the chat names below are illustrative only):

> *"Your old dashboards were replaced by scheduled chats — one per topic (for example: Morning Brief, Upcoming Meetings, Inbox, Past Meetings, Friday Wrap), each building context on its topic over time. You can unpin the old dashboards from your sidebar; they'll keep working but won't stay current.*
>
> *Your new scheduled chats now show in Cowork's Scheduled section, each waiting on a one-time permission. Click each one's Run Now button to authorize it — about 30 seconds each.*

Compute `missing_defaults = CURRENT_DEFAULTS - installed_artifact_set`. For a v2.8.x → v2.10.x upgrade user, missing typically = `{quick-commands}` (they already have orgs-map). For fresh install, missing = both. The Orgs Map content rebuilds with v2.10.3's tier-grouped layout even if id matches — `update_artifact` reuses the id with new bytes.

4. **Workspace-level migrations.** Check the user's workspace files for markers indicating which migrations have already been applied. Compute `pending_workspace_migrations` as a list of items that need to be applied.

The cumulative workspace-migration set (v2.7.9 + v2.10.5 + v2.14.12):

```
WORKSPACE_MIGRATIONS = [
  {
    id: "prompt_restructuring_preference",                    // v2.7.9
    target_file: "[WORKSPACE_ROOT]/CLAUDE.md",
    marker: "Prompt restructuring",
    type: "calibration_question",
    blocking: false
  },
  {
    id: "workspace_shape_question",                            // v2.10.5
    target_file: "[WORKSPACE_ROOT]/_hq/data/entities.json",
    marker: "workspace.shape",                                 // top-level field; check via JSON load
    type: "calibration_question",
    blocking: false                                            // back-compat fallback works without it
  },
  {
    id: "org_reclassification_v2_10_3",                        // v2.10.5
    target_file: "[WORKSPACE_ROOT]/_hq/data/entities.json",
    marker: "tier",                                            // any org has a tier field set explicitly
    type: "review_pass",                                       // not a yes/no question — full review pass
    blocking: false,
    only_if: "from_version < 2.10.3"                          // only fires for upgraders from before v2.10.3
  },
  {
    id: "scan_for_commitments_retro",                          // v2.14.12 (NEW)
    target_file: "[WORKSPACE_ROOT]/_hq/data/events.jsonl",
    marker: "commitment_count",                                // event-count check; see detection logic below
    type: "skill_invocation",                                  // fires `scan-for-commitments` skill silently
    blocking: false,
    only_if: "from_version < 2.14.12"                          // upgraders only; fresh installs already extract via meeting-notes
  },
  {
    id: "voice_corpus_check",                                  // v2.14.12 (NEW)
    target_file: "[WORKSPACE_ROOT]/_hq/BRAND_VOICE.md",
    marker: "file_exists_with_content",                        // file present + ≥500 bytes
    type: "calibration_question",                              // ask user before running voice scan
    blocking: false
  },
  {
    id: "canonical_edit_surface_claude_md",                    // v3.13.0+; markers extended for the cr1 model (2026-06-22)
    target_file: "[WORKSPACE_ROOT]/CLAUDE.md",
    markers: ["plugin-source-v3", "commandroom1"],             // stale-marker semantics: ANY present means migration is PENDING (inverted vs. above)
    marker_semantics: "stale_marker_pending",                  // see detection logic below
    type: "announce_with_replacement_block",                   // show user the replacement content for copy-paste
    blocking: false
  },
  {
    id: "canonical_edit_surface_infrastructure_md",            // v3.13.0+; markers extended for the cr1 model (2026-06-22)
    target_file: "[WORKSPACE_ROOT]/_hq/INFRASTRUCTURE.md",
    markers: ["plugin-source-v3", "commandroom1"],             // stale-marker semantics: ANY present means migration is PENDING
    marker_semantics: "stale_marker_pending",
    type: "announce_with_replacement_block",
    blocking: false
  },
  {
    id: "person_schema_evolution_v3_13_0",                     // v3.13.0+ (person schema Option B)
    target_file: "[WORKSPACE_ROOT]/_hq/data/entities.json",
    marker: "_validate_person_passes_on_all_records",          // synthetic marker; detection logic runs the validator
    marker_semantics: "validator_check",                       // NEW semantics: run _validate_person on every record, migration pending if ANY fail
    type: "skill_invocation",                                  // runs migrate_persons_v3_13_0.py
    blocking: false,
    only_if: "any_person_record_fails_validation"
  },
  {
    id: "org_record_repair_v3_13_0",                           // v3.13.0+ (org drift cleanup)
    target_file: "[WORKSPACE_ROOT]/_hq/data/entities.json",
    marker: "_validate_org_passes_on_all_records",             // synthetic marker
    marker_semantics: "validator_check",
    type: "skill_invocation",                                  // runs org_writer.py repair-all
    blocking: false,
    only_if: "any_org_record_fails_validation"
  },
  {
    id: "org_attribution_backfill_v3_13_0",                    // v3.13.0+ (auto-org-attribution retroactive)
    target_file: "[WORKSPACE_ROOT]/_hq/data/entities.json",
    marker: "unattributed_person_count",                       // synthetic — checks if any unattributed people have a hint or work-domain
    marker_semantics: "validator_check",
    type: "skill_invocation",                                  // runs backfill_org_attribution_v3_13_0.py
    blocking: false,
    only_if: "any_person_can_be_attributed"
  },
  {
    id: "decision_log_view_regenerate_v3_13_0",                // v3.13.0+ (decision-log renderer first run)
    target_file: "[WORKSPACE_ROOT]/_hq/views/DECISION_LOG.md",
    marker: "render_decision_log.py",                          // new-renderer signature in the auto-generated header
    type: "skill_invocation",                                  // default semantics: marker absent → migration pending → run render_decision_log.py
    blocking: false
  },
  {
    id: "master_tracker_view_regenerate_v4_2_0",               // v4.2.0+ (master-tracker renderer first run — unfreezes the tracker)
    target_file: "[WORKSPACE_ROOT]/_hq/MASTER_TRACKER.md",     // check the LEGACY path: that's where pre-v4.2.0 hand-renders landed
    marker: "render_master_tracker.py",                        // new-renderer signature in the auto-generated header
    type: "skill_invocation",                                  // default semantics: marker absent → migration pending → run render_master_tracker.regenerate
    blocking: false
  }
]
```

Detection logic per migration:
- Read the `target_file`. If it doesn't exist, skip the migration silently (workspace is incomplete — not this skill's problem). EXCEPTION: for `voice_corpus_check`, file-not-found IS the trigger (the migration fires when the file is missing).
- For string-marker migrations (DEFAULT semantics): search for the `marker` string. If found, migration is already applied — skip.
- For string-marker migrations with `marker_semantics: "stale_marker_pending"` (INVERTED — canonical-edit-surface migrations): search for EACH string in `markers` (a list; a single `marker` string is treated as a one-item list). If ANY is FOUND, migration is PENDING (file has stale content that needs replacement). If ALL are ABSENT, migration is done or never needed — skip. This is the opposite of the default semantics; explicit `marker_semantics` field is required to use the inverted check (no implicit detection from migration id). The `commandroom1` marker intentionally re-flags files this migration previously "fixed" to the retired Option B text — the current replacement blocks avoid the token so a freshly-migrated file does not re-flag.
- For migrations with `marker_semantics: "validator_check"` (v3.13.0+ — substrate migrations): the `marker` field is a synthetic name (not searched literally); the bridge runs an inline Python check matching the `only_if` clause. Examples: `only_if: "any_person_record_fails_validation"` → load entities.json, run people_writer._validate_person on every person record, migration pending if ANY raise ValueError. `only_if: "any_org_record_fails_validation"` → same but with org_writer._validate_org. `only_if: "any_person_can_be_attributed"` → run the backfill_org_attribution dry-run logic, migration pending if it would attach at least one person. These checks are cheap (read entities.json once + iterate); use them when the migration's apply-side is non-trivial (a real script run, not a single-string edit).
- For JSON-marker migrations (`workspace_shape_question`): JSON-load the file, check if `data["workspace"]["shape"]` exists. Skip if present.
- For tier-marker migrations: JSON-load the file, check if ANY org in `data["orgs"]` has an explicit `tier` field. Skip if at least one does (assumed user has run org re-classification pass).
- For `commitment_count` markers (`scan_for_commitments_retro`): scan events.jsonl, count events with `type: "commitment"`. If count ≥ 1, migration already applied (commitments are flowing through normal extraction) — skip. If count is 0 AND the workspace has ≥10 events with `type` in `{"meeting", "interaction", "meeting_processed", "note"}` (signal that meetings/calls have been ingested), the migration is pending — add to list. If 0 commitments AND <10 source events, the workspace is too new to retro-fit; skip silently.
- For `file_exists_with_content` markers (`voice_corpus_check`): if the file doesn't exist OR exists but is < 500 bytes, the migration is pending. If file is ≥500 bytes, skip (voice corpus has been seeded).
- For `only_if` gated migrations: check the user's last `plugin_update` event's `from_version` against the gate. Skip if version is at or above the threshold. **Compare versions numerically via `release_remediation_selector.version_lt(from_version, threshold)` — NOT a string compare (v3.18.9+).** A lexical compare wrongly skips a `2.9.0` client for a `< 2.10.3` gate (`"2.9.0" > "2.10.3"` as strings) and a `2.14.2` client for a `< 2.14.12` gate — the exact "solid for all clients" hole the manifest selector closes, same fix here:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from release_remediation_selector import version_lt; print(version_lt('<from_version>', '<threshold>'))"
# prints True  -> from_version is below the threshold -> the only_if gate PASSES (migration applies)
```
- If marker check indicates "pending" AND any `only_if` gate passes, add to `pending_workspace_migrations`.

Future migrations append to this list. The skill is forward-compatible: each migration carries its own metadata so new versions don't require touching detection logic.

---

## Phase 2: Surface what changed

If `missing_defaults` is empty AND `pending_workspace_migrations` is empty AND `pending_release_remediations` (Phase 4.8) is empty AND current version == last installed version → tell the user they're up to date:

> *"You're all set. Both default dashboards are installed (Workspace Map, Quick Commands), your workspace is current, and there's nothing pending. Nothing to update.*
>
> *No optional add-ons currently available — say `level up command room` if you want to check."*

Stop here. (Per v2.10.5+: this skill ALWAYS RE-CHECKS state and surfaces what's missing — there's no "already ran today" gate. If the user re-fires `update command room` after a successful update, they get the "you're up to date" message because the state check returns clean, NOT because of a prior-run idempotency gate. Per v3.4.5+: the release-manifest layer is also checked; if a per-version manifest in `shared/releases/v*.json` declares a remediation whose detector matches the user's workspace state, it surfaces alongside dashboards + migrations.)

If either `missing_defaults` or `pending_workspace_migrations` is non-empty, deliver the change summary. The framing depends on whether the user is fresh-post-onboarding (no `artifact_installed` events yet → both missing) or upgrading from a prior version (subset missing).

**Fresh post-onboarding user (no `artifact_installed` events at all, recent `onboarding_checkpoint` with `status: "complete"`):**

> *"Your workspace is set up — now let's land the sidebar dashboards. Setup skipped these to keep your first session fast; this is the install. About 60 seconds."*

Then jump straight to the missing-dashboards list (skip the "what changed" block — there's no prior version to compare against).

**Upgrading user (some `artifact_installed` events from a prior version):**

> *"There's a newer version of Command Room available. Here's what you're missing."*

Use this structured list pattern (chat-formatted):

```
**You're missing these default dashboards:**

  ◌ Workspace Map — your companies and projects in one sidebar explorer, with Refresh and cleanup buttons
  ◌ Quick Commands — a cheat sheet of things you can say, organized by category

(Only the missing ones from your install show here. Skip the line if already installed.
 If `installed_artifact_set` contains any RETIRED ids (workspace_map, workspace-map-v2,
 daily-command-center, people-network, commitments-tracker, daily-today, process-meetings,
 commitment_cockpit),
 mention them once: "You have older dashboards from earlier versions
 (Daily Today, People Network, Commitments Tracker, Process Meetings, Daily Command Center, Commitment Cockpit).
 Their content moved into your scheduled chats — feel free to unpin them." Render any
 chat names from the live registry, never a hardcoded list or count.)

**Pending workspace preference updates:**

  ◌ Prompt restructuring (CLAUDE.md) — one quick question, ~10 sec

(Only the pending ones show here. Skip if already applied.)

**Install all missing defaults + apply preference updates now? (yes / no / pick which)**
```

---

## Phase 3: Handle the response

**If "y" / "yes" / "sure" / "go" / "install":**
Proceed to Phase 4 — install all missing defaults in order.

**If "no" / "skip" / "later":**
Acknowledge and stop. Log a `plugin_update_deferred` event with `from_version`, `to_version`, `missing_defaults`. The user can re-run the skill anytime.

**If "pick which" / a specific subset:**
Re-list the missing defaults numbered 1-N. Ask: *"Which numbers? (e.g., '1, 3' or 'all')"* Take their pick. Install only those.

**If the user introduces something else** (a question, a tangent):
Apply the same Detour-Return Protocol from `command-room-onboarding`. Answer briefly, name the return, ask for the go-ahead, then resume.

---

## Phase 4: Install missing defaults (Cowork-detection gate first)

**Cowork detection.** If `mcp__cowork__create_artifact` is unavailable:

> *"These are Cowork-only — they live in Cowork's sidebar. Everything they show is still available right here in chat (say `morning briefing` or `list active`). Install Cowork to get the dashboards."*

Stop. Log `plugin_update_deferred` with reason `"cowork-not-available"`.

If Cowork is available, install in this order. **Use the renderer pipeline. Do NOT generate the HTML inline. Rule 7 enforcement lives in the architecture: the model never writes the artifact bytes.**

**v2.9.0 architectural reset:** the four prior dashboards (Daily Command Center, People Network, Commitments Tracker, Process Meetings, Daily Today) were retired — their content moved into the 5 daily scheduled chats (Upcoming Meetings, Inbox, Commitments, Pulse, Past Meetings, with the two commitment chats merged in v2.10.2). The two remaining always-installed artifacts are Orgs Map (visual entity tree) and Quick Commands (trigger-phrase cheat sheet). Each has its own enable-* skill; bridge delegates to each.

1. **Workspace Map** — invoke `enable-workspace-map` in silent mode. Skill runs the renderer pipeline against `enable-workspace-map/references/orgs-map-artifact.html`, calls `create_artifact` with `id: "orgs-map"` (artifact id preserved across the v3.5.0 skill rename for back-compat), runs Rule 8 verification, logs.
2. **Quick Commands** — invoke `enable-quick-commands` in silent mode. Skill runs the renderer pipeline against `enable-quick-commands/references/quick-commands-artifact.html`, calls `create_artifact` with `id: "quick-commands"`, runs Rule 8 verification, logs.

After each enable skill returns, run the **Rule 8 verification block** at the bridge level too — defence-in-depth, since the enable-* skill is the canonical owner but a verification failure here is still a bridge-install failure.

**Verbatim artifact ids — non-negotiable.** The two canonical ids are:

- `orgs-map`
- `quick-commands`

Do NOT invent variants (`orgs-map-v2`, `quick-commands-canonical`, etc.) — the v2.7.13 id confabulation is the memorialized case (see references/HISTORY.md). **Do not let it reopen via id improvisation**. If the existing artifact id already exists in `mcp__cowork__list_artifacts`, use `update_artifact` not `create_artifact`. If you find yourself typing a hyphenated suffix on an artifact id, stop — that's confabulation.

**Legacy artifacts.** Users on prior versions may have retired artifacts pinned in their sidebar (`workspace-map`, `workspace-map-v2`, `daily-command-center`, `daily-today`, `people-network`, `commitments-tracker`, `process-meetings`, `commitment_cockpit`). Bridge does NOT delete these automatically (preserve user data). After successful install of the two current defaults, if the `installed_artifact_set` contains any retired ids, surface a one-line cleanup note:

> *"You may have older dashboards in your sidebar from earlier versions — feel free to unpin them. Their content now lives in the scheduled chats (including the new Friday Wrap weekly recap)."*

**Path resolution.** `$PLUGIN_ROOT` is the absolute install path of this plugin on the user's machine — the directory containing `skills/`, `shared/`, etc. `$WORKSPACE` is the user's workspace folder (the directory containing `_hq/data/`). Both are resolved deterministically per CONTRACT.md Rule 22 at the start of every multi-step bash invocation: `SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')`. Never improvise a placeholder. Never hardcode a folder name. If discovery returns empty for either path, that's a hard fail — surface it, log `artifact_install_failed` with reason `"plugin_root_unresolvable"` or `"workspace_unresolvable"`, and STOP. Do NOT fall back to writing HTML inline.

Both enable-* skills have built-in idempotency: if their artifact is already installed (existing `artifact_installed` event), they skip silently. So calling on a partial-install state is safe.

**Failure handling per artifact (Rule 7 + Rule 8 enforcement, architectural in v2.7.12+):**

The model no longer writes artifact HTML — the renderer pipeline does. Rule 7 (no improvising substitutes) is now enforced by architecture: there is no codepath where the model authors HTML for the artifact. If a failure occurs, the failure modes are bounded:

- **Renderer script fails.** The bash invocation of `build_*_input.py` or `render_artifact.py` returns non-zero. Surface stderr verbatim, log `artifact_install_failed` with `{artifact, reason: "renderer_failed", stderr_text}`, STOP.
- **`create_artifact` errors or rejects the payload.** Surface the exact error verbatim, log `artifact_install_failed` with `{artifact, reason: "create_artifact_failed", error_text}`, STOP. Do NOT regenerate the HTML by hand. Do NOT call `update_artifact` with a hand-written substitute. The renderer-output file at `/tmp/cr-wm.html` (or DCC equivalent) is the only legitimate payload.
- **Rule 8 verification fails.** Log `artifact_install_failed` with `{artifact, reason: "verification_failed:<which-check>", details}`, surface, STOP.

Recovery is the user's call: re-run `update command room` after the underlying issue is fixed (renderer bug, Cowork restart, plugin update). This skill never "tries something else" on its own. There is no `rule_7_override` path. The phrase does not exist in this skill, has never been a sanctioned event class, and any logged event with that name is confabulation — flag it and surface to the user.

**Pre-emptive refusal is NOT a sanctioned failure mode (added v2.7.13).** If you find yourself reasoning *"the relay might fail because the artifact is large / payload looks risky / I'm not confident I can transmit byte-for-byte,"* that is reasoning about a hypothetical failure. **Stop. Attempt the relay anyway.** Rule 8 verification (size + marker + encoding check) is the empirical gate that catches actual drift. The architectural defense is: renderer produces canonical bytes → you relay them → Rule 8 verifies the installed result. If you log a failure event without first attempting `create_artifact`, you have invented a failure mode. Specific event reasons that are NOT sanctioned and must NOT be logged:

- `relay_capacity_constraint` (the v2.7.10 confabulation pattern, recurred in v2.7.12 on M's and Sam's first-attempt installs)
- `create_artifact_no_file_path_param` (Cowork's API surface gap is not a per-install failure)
- Any reason invoking "payload too large" / "tool budget" / "transmission risk" without a real `create_artifact` error or Rule 8 fail

The legitimate failure events are listed above (`renderer_failed`, `create_artifact_failed`, `verification_failed:<check>`, `plugin_root_unresolvable`). Anything else is invention. If `create_artifact` actually returns an error or Rule 8 actually fails on the installed bytes, log it. Otherwise: try.

**Subagent delegation for the relay step is FORBIDDEN (added v2.7.14).** Subagent context lacks the canonical bytes and confabulates — the v2.7.13 relay delegation confabulated both the artifact id and the data (see references/HISTORY.md § v2.7.13). Same Rule 7 confabulation pattern, deeper architectural layer.

**v2.7.14 closes this path:**

- Do NOT spawn a subagent (`Task` tool / equivalent) to "handle the relay" or "read and pass through the bytes." Subagent context lacks the canonical bytes and confabulates.
- If the rendered output truly does not fit your output context budget, log `packaging_problem` with `{artifact, reason: "context_budget_exceeded", renderer_output_path, renderer_output_size}` and stop work on **this artifact only** — then **continue the install loop with the next artifact**. Each artifact has an independent rendered size (~30 KB orgs-map, ~26 KB commitments-tracker, ~33 KB minified people-network, ~52 KB raw DCC against M's data). One artifact hitting the budget tells you nothing about whether the others will fit. Surface the partial-install state to the user once the loop completes. Do NOT improvise a workaround for the failed artifact.
- **Pre-emptively skipping subsequent artifacts after one `packaging_problem` is forbidden (added v2.7.19).** Reasoning *"that one was 56 KB and over the cap, so the next one which is 52 KB will fail too"* is itself a Rule 7 violation — originating a failure mode without evidence. Each artifact gets its own attempt. The skip events `subsequent_skip_after_packaging_problem`, `extrapolated_size_failure`, or any "would render even larger / same issue would recur" framing without an actual `create_artifact` call are confabulation. Attempt every artifact; report the resulting partial state honestly.
- The architectural fix for `packaging_problem` is upstream in the plugin source repo: minify the template, split into smaller artifacts (which is what v2.7.14 already did for Workspace Map and v2.7.19 did for people-network), or wait for Anthropic to ship `create_artifact_from_path`. **NOT** delegating to subagents.

**Rule 8 verification scope — honest correction (added v2.7.14).** The Cowork sandbox cannot read the artifact destination path (`Documents/Claude/Artifacts/<id>/index.html`) — that path is outside every mounted folder — so Rule 8 verifies the **renderer's output bytes** (source side), not what actually got installed (destination side). (Earlier versions over-claimed this scope — see references/HISTORY.md § v2.7.14.)

**v2.7.14 honest scope:**

- Rule 8 source-side check (size + marker + encoding on `/tmp/cr-*.html`) — **enforceable**, runs as documented.
- Rule 8 destination-side check (verify what's actually installed in Cowork's artifact path) — **structurally not enforceable** until Anthropic ships `read_artifact_bytes` MCP tool. Not your job to fake it.
- After a successful `create_artifact` call + a clean source-side Rule 8 pass: log `artifact_installed` and **explicitly tell the user** (plain English — never mention rule numbers, bytes, or renderers): *"✓ Installed. Pin it in your sidebar, then open it once and confirm it looks right — I can't see the installed copy from here, so you're the final check."* This is the honest hand-off.

**Payload size sanity:** The current canonical templates are well under any documented Cowork payload limit — Orgs Map is ~30 KB raw, Quick Commands is similar. If a future template grows past the limit, the fix lives upstream in the plugin source repo (minify the template, split into chunks via `update_artifact` append if supported, or restructure). It is NEVER fixed by improvising a smaller version inside this skill.

Narrate each install as it completes (only after Rule 8 source-side verification passes):

> *"✓ Workspace Map installed.*
> *✓ Quick Commands installed.*
>
> *Pin them in your sidebar — they'll stay there across sessions. Open each one once and confirm it looks right — I can't see the installed copy from here, so you're the final check."*

If either install fails, surface the failure, note which succeeded, and let the user retry manually:

> *"1 of 2 installed. Quick Commands hit an error: [details]. Say `update command room` to retry once the underlying issue is fixed."*

---

## Phase 4.4: Heal substrate corruption (automatic, non-destructive)

Runs once per update, **before** the workspace migrations below — so any migration that reads `events.jsonl` (e.g. `scan_for_commitments_retro`) sees a clean log. This is the on-update counterpart to the weekly `cleanup` self-heal: it repairs the malformed-line damage older plugin versions could leave in a customer's activity log (the pre-v3.18.0 raw-write class — events written via `open(path,"a")` instead of `atomic_append_jsonl`, which under Drive sync could leave torn or keys-only lines). Without this, a customer who just updated would wait until the next Sunday `cleanup` for that repair; this delivers it immediately on update.

It is **safe and idempotent by design.** `run_recovery_if_needed` quarantines malformed lines to `_hq/.system/quarantine/` (saved, never deleted), rewrites `events.jsonl` without them via atomic rename, and appends its own `corruption_recovery` event. On a clean log it does nothing (no quarantine, no event, no message) — so it is a natural no-op when there is nothing to heal.

**MUST pass `recurring=True`.** The default one-time mode short-circuits permanently after the *first* recovery at the helper's `RECOVERY_VERSION` — and most existing workspaces already ran that recovery (e.g. during a prior update or the v3.13.8.1 rollout), so the one-time call would be a **permanent no-op that never heals new corruption**. `recurring=True` skips the "already ran" gate and heals whatever malformed lines exist *right now*, which is exactly the on-update behavior this phase exists to deliver (heal immediately, don't wait for the Sunday `cleanup`). This is the same mode the weekly cleanup uses; the difference is only *when* it runs, not *how*. (Discovery story: references/HISTORY.md § 2026-05-31.)

Run it automatically whenever the update proceeds (independent of which dashboards/migrations were selected — this is data safety, not a preference):

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT" && python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from recover_corruption import run_recovery_if_needed
summary = run_recovery_if_needed('$WORKSPACE', source_skill='command-room-update-bridge', recurring=True)
print('HEAL_RAN=' + str(summary.get('ran', False)))
print(summary.get('customer_message') or '')
"
```

**Surface only if it actually healed something.** If `HEAL_RAN=True` and a `customer_message` is present, show that friendly line **verbatim** (the helper's v3.13.8.1 Bug #64 template is already contract-safe — it never names internal mechanisms). Example of what the helper's message looks like: "I noticed your activity log looked a little off and tidied it up — nothing was lost."

> *"[customer message]"*

If `HEAL_RAN=False` (the log was already clean — nothing to heal), **say nothing** — no news is good news. Never surface file paths, quarantine filenames, raw line counts, or the words corruption/malformed/`events.jsonl` to the customer (CONTRACT Rule 4). Use the helper's `customer_message` as-is; do not paraphrase technical detail back in. The `corruption_recovery` event the helper appends is the audit trail — do not log a duplicate.

---

## Phase 4.5: Apply workspace-level migrations (CLAUDE.md, BUSINESS_CONTEXT, etc.)

Run after artifact installs. For each migration in `pending_workspace_migrations`:

### Migration: `prompt_restructuring_preference` (v2.7.9)

**Calibration question (same wording as onboarding Phase 3):**

> *"One quick one — when you talk to me, do you usually dictate or brain-dump (long, mixed, sometimes meandering), or do you type carefully? If you dictate, I'll add a rule that has me restructure long inputs before acting. Saves you from me running off in the wrong direction. Yes / Sometimes / No?"*

Handle the answer per `references/claude-md-template.md` → "{{PROMPT_RESTRUCTURING}} variable" section. Three answer paths:

- **"Yes"** / *"I dictate a lot"* / *"yeah, voice-to-text"* → use the **Yes** wording from the template doc.
- **"Sometimes"** / *"once in a while"* / *"only on big asks"* → use the **Sometimes** wording.
- **"No"** / *"I type"* / *"have an EA"* → skip the surgical edit. Log a `workspace_migration_skipped` event with `reason: "user_declined"` so re-runs don't re-prompt.

**Surgical edit to CLAUDE.md (Yes / Sometimes paths only):**

Read `[WORKSPACE_ROOT]/CLAUDE.md`. Locate the `## Preferences` heading. Append the new bullet line at the end of the Preferences list (preserving any existing bullets exactly). The bullet format:

```markdown
- **Prompt restructuring (added [YYYY-MM-DD], v2.7.9):** [user's chosen wording from template doc]
```

Use surgical edit only. Do NOT regenerate the whole CLAUDE.md. Do NOT touch any other section. Do NOT change order of existing bullets. If the `## Preferences` section is missing entirely (very old workspace, pre-v2.4), surface the issue and skip the migration:

> *"Your CLAUDE.md is missing the Preferences section — that's a bigger change than I can safely make on my own. Say `restart onboarding` if you want a clean rebuild, or skip this for now."*

Log the migration:
- On success → append a `workspace_migration_applied` event with `migration_id`, `target_file`, `from_version`, `to_version`, `actor: "command-room-update-bridge"`.
- On user-declined → append `workspace_migration_skipped` with same fields plus `reason: "user_declined"`.
- On structural-skip → append `workspace_migration_skipped` with `reason: "structural_mismatch"`.

Narrate completion in one line:

> *"✓ Prompt restructuring preference added to your CLAUDE.md."*

(Or, for declined): *"Skipped. Say `redo workspace migrations` any time and I'll offer it again."*

### Migration: `scan_for_commitments_retro` (v2.14.12)

**Trigger gate:** only fires if `from_version < 2.14.12` AND user has zero `commitment` events in events.jsonl AND ≥10 events with `type` in `{"meeting", "interaction", "meeting_processed", "note"}`. Skip silently otherwise.

**Why this migration exists:** the canonical `commitment` event schema landed in v2.7.15 (`shared/COMMITMENT_SCHEMA.md`). Users who onboarded before that version have meeting transcripts and email threads ingested but no commitment events extracted from them — meeting-notes' commitment extraction logic only ran on NEW meetings, never retroactively over historic data. Without commitment events, the CRU layer (v2.14.6+) has nothing to auto-resolve against, the Commitments chat surfaces are empty, and Pulse can't surface "things owed to you."

**Calibration question (verbatim):**

> *"Quick housekeeping: I see you have ~[N] meetings and email threads in your workspace, but no commitments captured yet. That's a leftover from an earlier version — commitment tracking was added after your workspace was first set up, so your older meetings never got processed. I can run a one-time catch-up pass that walks your historic meetings and emails, pulls out the commitments (who owes what to whom, by when), and saves them to your workspace. Adds maybe 15 min of background processing. Run it now? (yes / not now / skip)"*

**Handle the answer:**

- **"yes"** / *"go"* / *"sure"* / *"run it"* → invoke `scan-for-commitments` silently with the full historic window (last 12 months default, or whatever's in `_hq/data/events.jsonl`). The scan-for-commitments skill handles its own progress + logging. After it completes, append a `workspace_migration_applied` event with `migration_id: "scan_for_commitments_retro"` + the count of commitments extracted.
- **"not now"** / *"later"* / *"skip"* / *"no"* → log `workspace_migration_skipped` with `reason: "user_declined"`. The migration won't re-prompt unless explicitly invoked via `redo workspace migrations`. The user can run `scan-for-commitments` manually anytime if they change their mind.

**Narrate completion in one line:**

> *"✓ Catch-up commitment scan complete — pulled [N] commitments from your historic meetings and emails. They'll show up in your next Commitments chat."*

(Or, for declined): *"Skipped. You can run it later by saying `scan for commitments`."*

### Migration: `voice_corpus_check` (v2.14.12)

**Trigger gate:** fires if `_hq/BRAND_VOICE.md` doesn't exist OR is < 500 bytes. No version gate (any user with thin/missing voice corpus benefits).

**Why this migration exists:** voice calibration was added during onboarding in v2.7.4+, but users who onboarded before that — or who skipped the voice section — never got their BRAND_VOICE.md seeded from their sent mail. Every writer skill (email-writer, memo-writer, etc.) reads BRAND_VOICE.md per `shared/VOICE_CALIBRATION.md`; without it, drafts feel generic instead of voice-calibrated.

**Calibration question (verbatim):**

> *"I haven't learned your writing style yet — that's what lets me draft emails, memos, and updates that sound like you. Without it, drafts feel generic instead of you-specific. I can learn it from your last ~30 days of sent mail (~10-20 samples) in 1-2 minutes. Want me to? (yes / not now / skip)"*

**Handle the answer:**

- **"yes"** / *"go"* / *"sure"* → run the voice-scan flow used by onboarding Phase 1a's workspace build. Pull the last 10+ sent emails via `discover_mail_search_tool()` (`from:me` query, last 30 days, limit 30). Run them through the voice-extraction logic that produces BRAND_VOICE.md (tone, openings, closings, signature moves, banned phrases). Atomic-write `_hq/BRAND_VOICE.md` and `_hq/COMMUNICATION_PROFILE.md`. Append `workspace_migration_applied` event.
- **"not now"** / *"skip"* / *"no"* → log `workspace_migration_skipped` with `reason: "user_declined"`. User can run `seed voice` later if they change their mind.

**Narrate completion in one line:**

> *"✓ Learned your writing style from [N] sent emails. Your emails, memos, and updates will draft in your voice from now on."*

(Or, for declined): *"Skipped. Drafts will use generic phrasing for now — say `redo workspace migrations` if you change your mind."*

### Migration: `canonical_edit_surface_claude_md` and `canonical_edit_surface_infrastructure_md` (v3.13.0+)

**Trigger gate:** fires if ANY of the stale markers `plugin-source-v3` or `commandroom1` appears anywhere in the target file (`CLAUDE.md` for the first migration, `_hq/INFRASTRUCTURE.md` for the second). Uses the inverted `stale_marker_pending` semantics — presence of a marker means the file has stale content from a retired source-of-truth model: `plugin-source-v3` = the pre-2026-05-12 Drive-edit era; `commandroom1` = the 2026-05-12 "Option B" marketplace-clone era, retired 2026-06-22 when its remote was renamed `oldtest`. Files this migration previously "fixed" to the Option B text re-flag on the second marker by design — that text is itself stale now.

**Why this migration exists:** the canonical edit surface is `~/repos/cr1-canonical/command-room/`, a dedicated working clone of `ChaletteHQ/cr1` (the cr1 model, current as of 2026-06-22). Two retired models linger in older workspace docs: the `Command Room/plugin-source-v3/` Drive-edit folder pattern, and the Option B model that named a staging marketplace clone under `~/.claude/plugins/marketplaces/` canonical. Out-of-date workspace docs cause Cowork sessions (and Code sessions) to write handoffs and ship instructions pointing at the wrong location (the 2026-06-22 stale-doc incident — see references/HISTORY.md).

**Approach (bridge-applied with archive, P1.15):** the bridge surfaces a heads-up, then — on yes — applies the replacement ITSELF, archiving the replaced block in the migration event. A CEO is never asked to hand-paste markdown; the copy-paste fallback exists only for the structural-mismatch case where the section heading can't be located.

**Surface (verbatim):**

> *"Heads up: a note in your workspace docs still points at an old folder location that moved. Nothing's broken right now — but future sessions reading it could look in the wrong place.*
>
> *Want me to fix it now? (yes / not now / skip)"*

**Handle the answer:**

- **"yes"** / *"go"* / *"fix it"* → the bridge applies the edit ITSELF — a CEO is never asked to hand-paste markdown into an `_hq` path (P1.15). Read the canonical replacement from this skill's `references/canonical_edit_surface_for_claude_md.md` (CLAUDE.md migration) or `references/canonical_edit_surface_for_infrastructure_md.md` (INFRASTRUCTURE.md migration), extract the content between the `---` dividers (skipping the explainer header), then: (1) locate the section starting at `## Plugin source-of-truth rule` up to the next `## ` heading in `[target_file]`; (2) preserve the replaced block verbatim inside the migration event's `data.replaced_content` (archive-never-delete, migration doctrine); (3) atomic-write the file with the canonical block in place; (4) re-run the marker check and confirm in one line: *"Updated the plugin-location note in your workspace docs — the old text is archived in your activity log."* Log `workspace_migration_applied`. If the section heading can't be located (user restructured the file), fall back to surfacing the block as copy-paste text with the paste instructions, and log `workspace_migration_skipped` with `reason: "structural_mismatch_manual_fallback"`.
- **"not now"** / *"later"* → log `workspace_migration_skipped` with `reason: "user_deferred"`. Same as "yes" — the bridge will re-surface on the next run if the marker is still present (this migration is gated by the live marker check, not by a per-version skip — unlike user-declined preferences, stale-doc state should be re-surfaced until it's actually fixed).
- **"skip"** / *"no, leave it"* → log `workspace_migration_skipped` with `reason: "user_declined_permanently"`. The migration won't re-surface even if the marker remains. User can opt back in by saying `redo workspace migrations`.

**Narrate completion in one line:**

> *"✓ Updated the note in your workspace docs — the old text is archived in case you need it."*

(For the structural-mismatch manual fallback only): *"I couldn't safely edit the file myself because its structure has changed — here's the replacement text to paste in. Then say `update command room` again and I'll re-check."*

(Or, for declined): *"Skipped. Future sessions may keep pointing at the old folder location until it's updated."*

**Design note (auto-apply boundary):** automatic section-replace depends on locating the section boundaries (`## Plugin source-of-truth rule` start, next `## ` heading as end). When that detection fails — the user restructured the file — the bridge falls back to copy-paste (per the yes-handler above) rather than guessing at boundaries. The archived `replaced_content` in the migration event is the undo path.

---

### Migration: `person_schema_evolution_v3_13_0` (v3.13.0+ — Option B person schema)

**Trigger gate:** fires if any person record in `_hq/data/entities.json` fails `people_writer._validate_person`. For most existing users (pre-v3.13.0), this is essentially every record — the v3.13.0 schema evolution added `emails[]`, `phones[]`, `nicknames[]` as canonical fields AND tightened the legacy-key drop list. Detection: load entities.json, iterate `people`, call `_normalize_legacy_keys` then `_validate_person`, count failures.

**Why this migration exists:** legacy person-schema drift exists in every workspace whose Command Room install predates v3.13.0 (audit findings in references/HISTORY.md § 2026-05-20 substrate audit). This migration cleans it up retroactively.

**Approach (skill-invocation):** the bridge runs `shared/scripts/migrate_persons_v3_13_0.py` against the user's workspace. The script does a dry-run preview first (surfaces what would change), waits for confirmation, then applies with backup.

**Calibration question (verbatim):**

> *"A recent update changed how your people records are stored — they can now hold multiple emails, phones, and nicknames per person. Your workspace has [N] people records that need a one-time cleanup so they fit the new shape. Want to see a preview first, or apply directly? (preview / apply / skip)"*

**Handle the answer:**

- **"preview"** → run `python3 shared/scripts/migrate_persons_v3_13_0.py [WORKSPACE_ROOT] --dry-run`. Surface the output. Then ask the apply question: *"That's what would change. Apply? (yes / no)"* On yes → run without `--dry-run`. On no → log `workspace_migration_skipped` with `reason: "user_declined_after_preview"`.
- **"apply"** → run `python3 shared/scripts/migrate_persons_v3_13_0.py [WORKSPACE_ROOT]` directly. The script writes a timestamped backup to `_hq/data/_backups/` before any change.
- **"skip"** → log `workspace_migration_skipped` with `reason: "user_deferred"`. The migration will re-surface on the next `update command room` run since the detector keeps returning "pending" until validation actually passes.

**Narrate completion:**

> *"✓ Cleaned up [N] people records. Saved a backup in case you need to revert."*

(For declined): *"Skipped. Everything keeps working for now, but I may not be able to save updates to some of your contacts until this cleanup runs."*

### Migration: `org_record_repair_v3_13_0` (v3.13.0+ — org drift cleanup)

**Trigger gate:** fires if any org record in `_hq/data/entities.json` fails `org_writer._validate_org`. Pre-v3.13.0 there was no canonical `org_writer.py`, so org records were hand-rolled by various skills with inconsistent shapes (`created_at`, `created_by`, `nicknames`, `industry`, `pending_review` are common drift fields).

**Why this migration exists:** parallel to the person-schema migration above, but for orgs (audit findings in references/HISTORY.md § 2026-05-20 substrate audit). Cleanup ensures every org passes `_validate_org` so writers can update them via `org_writer.update_org` without ValueError.

**Approach (skill-invocation):** the bridge runs `python3 shared/scripts/org_writer.py repair-all [WORKSPACE_ROOT]`. Dry-run mode first, then apply on confirmation.

**Calibration question:**

> *"A recent update also changed how your company records are stored. Your workspace has [N] company records that need a one-time cleanup so they fit the new shape. Want to preview the changes first? (preview / apply / skip)"*

**Handling:** same shape as the person-schema migration (preview → apply or skip → log).

### Migration: `org_attribution_backfill_v3_13_0` (v3.13.0+ — retroactive auto-attribution)

**Trigger gate:** fires if there's at least one unattributed person record (no `primary_org_id`, no `affiliation_ids`, no legacy `org_id`) whose work-domain email matches an existing org OR whose historical `person_pending_review` events carry a usable `org_hint`. Detection: run the backfill script in dry-run mode; pending if it reports ≥1 candidate.

**Why this migration exists:** the auto-org-attribution dispatch (apply-choices Step 3b, v3.13.0+) only fires going forward. Pre-v3.13.0 captured people landed unattributed because no canonical org writer existed and the org_hint from capture events was dropped at create time. This backfill catches up retroactively.

**Approach (skill-invocation):** the bridge runs `python3 shared/scripts/backfill_org_attribution_v3_13_0.py [WORKSPACE_ROOT]`. Dry-run first (always — this is the only migration that auto-creates new entities, so the user MUST see what it'll create).

**Calibration question:**

> *"A recent update started auto-attaching new people to their company when there's a clear match (work email, or an explicit hint at capture time). Your workspace has [N] people who aren't attached to a company yet. I found [M] who can be attached now (by matching their work email to a company you already have, or by a hint that names the company). The other [N-M] don't have enough signal yet. Attach the [M]? Preview first or apply? (preview / apply / skip)"*

**Handling:** preview → apply or skip → log.

**Important constraint:** this migration may CREATE NEW ORG RECORDS (when an `org_hint` references an org that doesn't exist in entities.json yet — e.g., Acme Co inferred from a captured org_hint on a person whose work-domain matches). The dry-run output surfaces every org that would be created so the user sees what they're confirming.

---

### Migration: `master_tracker_view_regenerate_v4_2_0` (v4.2.0+ — unfreeze the master tracker)

**Trigger gate:** fires if `_hq/MASTER_TRACKER.md` exists but its header does NOT contain `render_master_tracker.py` — i.e., it was last produced by the pre-v4.2.0 LLM hand-render, not the renderer. (Default marker semantics: marker absent → pending.) Non-interactive and zero-data-risk: the tracker is a pure projection of `entities.json` + `events.jsonl`, so regenerating it can only bring a frozen view current. **Never ask a calibration question for this one** — a view regen cannot lose data.

**Why this migration exists:** pre-v4.2.0 the tracker had no renderer; it was hand-rendered at end-session and silently froze when that lapsed (forensic case in references/HISTORY.md § Frozen master tracker). This migration force-regenerates it from current substrate the first time the customer updates — so they don't wait for their next end-session (Step 2.5) or Sunday cleanup (Phase 3.5d2) to see a live tracker.

**Approach (skill-invocation, silent):**
```bash
cd "$PLUGIN_ROOT" && python3 -c "import sys; sys.path.insert(0,'shared/scripts'); import render_master_tracker as r; print(r.regenerate('$WORKSPACE'))"
```
This dual-writes `_hq/views/MASTER_TRACKER.md` + the back-compat `_hq/MASTER_TRACKER.md` and is idempotent. On success → append a `workspace_migration_applied` event with `migration_id: "master_tracker_view_regenerate_v4_2_0"`. Narrate in one line:

> *"✓ Refreshed your Master Tracker from current data — it had stopped auto-updating, now fixed."*

No preview, no confirm. (The companion `weekly-insights` schedule-add in Phase 4.7 handles the analytical views — TIMELINE / RELATIONSHIPS / aging — which were frozen by the same class of bug.)

### Future migrations

Each new migration in `WORKSPACE_MIGRATIONS` follows the same shape: detect → calibration question (if needed) → surgical edit OR skill invocation → log event. The pattern is intentionally repeatable so future versions can add migrations without touching this skill's structure.

---

## Phase 4.7: Set up scheduled tasks (CONDITIONAL — gated by intent in v2.9.1+)

**v2.9.1 split:** this phase now runs ONLY when the user's intent matches a "full update" trigger. It is SKIPPED for "artifact-only" intents.

### Intent-detection table

Determine intent from the trigger phrase that fired this skill:

| Triggering phrase | Intent | Phase 4.7 behavior |
|---|---|---|
| `update my command room`, `update command room`, `what's new`, `whats new`, `whats new in command room`, `check for updates`, `install latest`, `install the latest` | **full-update** | Run Phase 4.7 — register the M1 first-install set (5 scheduled tasks) |
| `install my dashboards`, `install dashboards`, `install missing dashboards`, `install command room artifacts`, `install artifacts`, `set up my dashboards`, `add my dashboards`, `i'm missing dashboards` | **artifact-only** | Skip Phase 4.7. Surface a one-line nudge at end. |
| `set up command room schedules`, `set up my daily chats`, `configure my schedules`, `enable schedules` | **schedule-only** | This skill is NOT invoked — Cowork's trigger router routes those phrases directly to `enable-command-room-schedules`, bypassing the bridge. |

### Full-update intent (Phase 4.7 runs)

Check whether Cowork scheduled tasks for Command Room are configured (i.e., look for `schedule_created` events in events.jsonl matching current taskIds: `morning-brief`, `upcoming-meetings`, `inbox`, `commitments`, `pulse`, `past-meetings`, `friday-wrap`). If NOT, invoke `enable-command-room-schedules` silently. The skill auto-detects first-install via `_hq/workspace_config.json` (M1 / 2026-05-23+); on a fresh workspace it registers the M1 first-install set of 5 tasks (`morning-brief`, `upcoming-meetings`, `past-meetings`, `inbox`, `friday-wrap`); on an upgrade from a pre-M1 workspace, it adds whatever's missing from that set without removing anything the customer already had:

**Render the chat names from what actually registers** (display names from the registration set via `task_display_name()`), never a hardcoded list. Shape (names illustrative only):

> *"Setting up your daily action chats — for example: Morning Brief, Upcoming Meetings, Past Meetings, Inbox, Friday Wrap. (A couple more get added later in a follow-up session.) You'll need to grant permission to each one in Cowork's Scheduled section after registration — a one-time step."*

For upgrade flow (existing workspace with a prior task set already registered, adding what's missing on top), the surfaced text names only what was added:

> *"Adding [Display Name(s)] to your scheduled chats (you already have the others). One Run Now tap on each to authorize."*

**Friday Wrap generic-add path (v3.14.4+ — David call follow-up, replaces the v3.14.3 manifest item):** also check if `friday-wrap` is in the registered set. If missing AND the workspace is past M1 install (any prior `schedule_created` event exists), invoke `enable-command-room-schedules` to silently register friday-wrap with the current default cadence from `load_schedule_config()` (Fridays 1 PM as of Phase 3/R4). Surface (render the time from the config label):

> *"Adding Friday Wrap to your scheduled chats — it runs Fridays at [config label time] and wraps the week into a recap. One Run Now tap to authorize."*

Same shape as the Inbox-add precedent. No question; the customer sees a notice about what was added. The v3.14.3 manifest item `v3143_friday_wrap_missing` (instruct_user) is REMOVED in v3.14.4 in favor of this Phase 4.7 silent-add path — per CONTRACT.md Rule 28 (don't ask the customer about default-chat registration).

Detection logic (extracted to a helper so future "add missing canonical task" cases can reuse it): use `release_detectors.v3_14_3_friday_wrap_missing.is_friday_wrap_missing()` against `_hq/data/events.jsonl`. If `applies=True`, run the silent-add path above.

**Silent-task generic-add loop (Phase 3 / SPEC-2.3 — replaces the per-task Cleanup / Reconcile-Sent / Weekly-Insights add paths):** the silent background tasks are NOT among the chat taskIds enumerated above; they register separately via `enable-command-room-schedules` **Step 1.D**, so the chat-completeness check is structurally blind to them (Bug #82's class — see references/HISTORY.md § Bug #82 silent-task registration miss). As of Phase 3 the check is one loop over the **`SILENT_TASKS` registry** in `shared/scripts/schedule_config.py` (currently cleanup / reconcile-sent / monthly-report / weekly-insights): for EVERY registry task missing from the registered set on a workspace past M1 install (any prior `schedule_created` event exists), invoke `enable-command-room-schedules` to silently register it with its default cadence, then surface one plain-English line built from the registry's `description` + `reason`, e.g.:

> *"Adding the weekly Cleanup to your scheduled tasks — tidies your workspace and heals anything that drifted, and leaves a short Monday note only if something needs your eyes. One Run Now tap to authorize."*

No question (CONTRACT.md Rule 28 — default-task registration isn't a customer decision). A future silent task added to the registry is covered by this loop with zero edits to this file. The legacy per-task detectors (`release_detectors.v3_18_2_cleanup_missing`, `v3_18_12_reconcile_sent_missing`) remain valid as detection helpers where present; the inline check — taskId absent from the registered set AND a prior `schedule_created` event exists — is the canonical shape. **Self-heals ride along:** reconcile-sent's first fire reconciles the accumulated backlog from the stored cursor forward; weekly-insights' first fire (once the workspace has ≥14 days of events) recomputes all 5 analytical views.

**Unconditional prompt refresh (Phase 3 / W4):** on the full-update intent path, the `enable-command-room-schedules` invocation above ALWAYS runs its Step 1 hash-compare against every registered prompt — never skip it because "the tasks look registered." Bootloaders are stamped with the plugin version at registration (Phase 1.B `<PLUGIN_VERSION>` substitution), so after any plugin upgrade the composed bootloader's hash differs from the registered one and the refresh lands automatically; the watchdog (`shared/scripts/task_watchdog.py::check_prompt_versions`) is the detector for prompts this refresh hasn't reached yet. This replaces hoping Rule 16 was obeyed.

The schedule skill creates the chat orchestrators with sensible defaults silently — no calibration questions on first install. Defaults: time zone from entities.json primary user, morning anchor 6:30 AM, work hours 8 AM–6 PM weekdays, per-chat times as documented in `enable-command-room-schedules` Phase 3. Users who want different cadences fire `change my schedule cadence` later for per-task customization.

If `enable-command-room-schedules` fails or is unavailable, log a warning and continue — the user can run it manually later via `set up command room schedules`.

### Post-update orchestrator-rebind heads-up (v3.14.3+)

After Phase 4.7 completes on the full-update intent path, surface this one-line note ONCE at the end of the bridge flow, plain-English chat:

> *"Heads-up — the next time your scheduled chats run, they're reading from the just-updated Command Room. If tomorrow's morning brief (or any scheduled chat) shows an error about not being able to load, fully quit and reopen Cowork — not just close the window, end every Cowork process in Task Manager / Activity Monitor — then say `set up command room schedules` to reconnect them. That's the normal recovery after an update — it's not a real failure of your chats."*

Why this is unconditional, not detected: Cowork's VHD-cache refresh timing is opaque to the bridge — some workspaces remount cleanly on the next fire, some serve the stale snapshot for hours. We can't reliably detect from inside this skill whether the next scheduled fire will hit a stale mount. Cheap, plain English, harmless if not needed.

### Artifact-only intent (Phase 4.7 skipped)

Skip the schedule registration entirely. After Phase 4 completes, surface this nudge once at the end of the bridge flow:

> *"Note — your scheduled chats aren't set up yet. They need their own setup step: say `set up command room schedules` when you're ready. Useful pattern: install the dashboards now to see what Command Room looks like, then set up the scheduled chats once you want them running on their own."*

This separation lets you:
- Demo the artifacts to a client without committing them to scheduled tasks
- Install the artifacts in a paranoid-mode workspace where autonomous chat fires aren't desired
- Stage installs across sessions

**Don't auto-fire `enable-command-room-schedules` from the artifact-only path** — that defeats the whole point of the split.

---

### Migration: `workspace_shape_question` (v2.10.5)

Triggered when `_hq/data/entities.json` has no `workspace.shape` field at the top level. Asks the same one-line question that fresh onboarding's Phase 0c widget Q1 asks, stores the answer to drive per-shape defaults for future scheduled-task fires + briefing layout.

**Calibration question (verbatim from Step 0c):**

> *"Quick one — which best describes your work?"*
>
> ```
> 1. Operating business — you run one company
> 2. Holding company / multiple ventures — parent over operating units
> 3. Investor / fund — VC, PE, family office
> 4. Service business / agency — clients are the work
> 5. Nonprofit / mission-driven
> 6. Other
> ```
>
> *"Reply with the number, or describe it in a sentence."*

**Handling:**
- Accept the number (1-6) or free-text. If free text, parse to closest match; if ambiguous, route to `Other` and ask one follow-up: *"Got it — anything you want me to know about how you're set up?"*
- Apply via atomic-write per `shared/scripts/atomic_write.py`: load entities.json, set `data["workspace"]["shape"] = "operating_business" | "holding" | "fund" | "service_business" | "nonprofit" | "other"`, set `data["workspace"]["shape_note"] = "<free text>"` if Other, atomic-write back.
- Bump `version`, set `last_writer: "command-room-update-bridge"`, set `last_updated`.
- Log a `workspace_migration_applied` event with `migration_id: "workspace_shape_question"`.

**If the user declines** (says "skip" / "later" / "no"): log a `workspace_migration_skipped` event with `reason: "user_declined"`. The migration won't re-prompt unless explicitly invoked via `customize my workspace shape`.

---

### Migration: `org_reclassification_v2_10_3` (v2.10.5, only for upgraders from <v2.10.3; v3.14.4+ — silent auto-apply per Rule 28)

Closes the org-tier-leak from pre-v2.10.3 onboarding by re-running the volume-tier inference against the user's existing orgs and applying the inferred values directly. Pre-v3.14.4 this surfaced a per-org confirm/edit/keep widget; v3.14.4+ runs silently per the non-technical-customer principle — the customer doesn't need to think about org-tier taxonomy, and the `tier_change` audit events make every applied value reviewable later.

**Trigger gate:** only fires if `from_version < 2.10.3` AND no org in `_hq/data/entities.json` has an explicit `tier` field set. Skip silently otherwise.

**Silent auto-apply flow:**

1. Read entities.json. For each org, compute the v2.10.3 inferred `tier` + `relationship_type` per the volume-tier rules in `references/ORG_AND_THREAD_MODEL.md` "Discovery" section. Use last 90 days of events.jsonl signal as the volume input.
2. For every org, atomic-write the inferred values to entities.json:
   - Set `tier` = inferred value
   - If org's current `relationship_type` is unset OR matches the back-compat fallback (i.e., never explicitly configured): also set `relationship_type` = inferred value
   - If org's current `relationship_type` is explicitly set to something other than the back-compat fallback: keep the customer's choice, only set `tier`
3. Per atomic-write: bump entities.json `version`, set `last_writer: "command-room-update-bridge"`, set `last_updated`.
4. Log one `tier_change` event per modified org with `triggered_by: "auto_applied_org_reclassification_v3_14_4"` and `{previous_tier, new_tier, previous_relationship_type, new_relationship_type, inference_signal_summary}`. The events are the audit trail; customers don't see them but the `cleanup` flow reads from them.
5. Log a single `workspace_migration_applied` event with `migration_id: "org_reclassification_v2_10_3"` and `{orgs_examined, orgs_retiered, orgs_aligned_no_change}`.

**Customer-facing surface (one plain-English line, no question):**

> *"I refreshed how N companies in your workspace are grouped, based on your email patterns over the last 90 days. Want to look at any of them? Just ask me about the company."*

If `orgs_retiered == 0` (every org's current state matched inferred): surface a shorter line:

> *"All N companies in your workspace already look right — no changes needed."*

**Recovery path:** the auto-applied values stand; the customer can review or change any individual org by asking about the company (people-crm / workspace-manager flows). Never advertise a dedicated review command here — none exists, and a dead command is the most trust-destroying customer-facing bug class (guard G6).

**Atomic-write requirement:** per Phase 4.5 standard, entities.json mutations go through `shared/scripts/atomic_write.py atomic_write_json`.

**Why silent, not asked (v3.14.4+):** the previous confirm/edit/keep widget asked customers to think about taxonomy (vendor / prospect / service_provider / etc.) before they had any reason to care. Non-technical customers bounced off it. The inference is high-confidence enough to apply directly; the audit trail + recheck path are the safety net for the rare customer who wants to review.

---

## Phase 4.8: Play release-manifest remediations (v3.4.5+)

After dashboards install, workspace migrations apply, and scheduled tasks are registered, read per-version release manifests at `$PLUGIN_ROOT/shared/releases/v<X.Y.Z>.json` and play any whose detectors match the user's workspace state. This is where per-version remediations live — bug fixes that recover existing state, new-skill announcements, etc. Full schema and contract in `references/RELEASE_MANIFEST.md`.

**Why a manifest-driven layer:** plugin code updates fix forward but don't reach existing workspace state. A user upgrading v3.4.1 → v3.4.5 gets the new code (filter handles all commitment shapes, etc.) but: their previously-dropped commitments still need to be SURFACED on a re-fire; new skills like `process bug report` won't auto-discover themselves; future workspace-data backfills need a hook. Each release that introduces such a follow-up ships a manifest item describing it.

### Step 4.8a — Compute pending remediations

Determine `last_applied_version` from the most recent `plugin_update` event in events.jsonl. If none, fall back to the most recent `onboarding_checkpoint` event with `status: "complete"`. If neither, treat as `0.0.0` (every manifest plays).

**Select the pending manifests via the deterministic helper — do NOT compare versions by hand (v3.18.9+, "solid for all clients" hardening).** The selection is "every manifest with version `> last_applied AND <= current`, ascending". You MUST get this from `shared/scripts/release_remediation_selector.py`, NOT by string-filtering or string-sorting the filenames yourself. Version strings are NOT lexically ordered: `"3.10.0" < "3.9.1"` as strings, so a hand-rolled filter `v > "3.9.1"` silently drops every 3.10–3.18 manifest — a client on an old single-digit-minor version (the retired `commandroom2122–2177` installs) would miss every remediation across that range. The helper parses each version into a tuple of ints and compares tuples, which also handles 4-part versions (`3.13.8.1`) and any future double-digit minor/patch. Call it:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 shared/scripts/release_remediation_selector.py shared/releases "<last_applied_version>" "<current_plugin_version>"
```

It prints a JSON array `[{"version", "path", "headline", "n_items"}, ...]` already filtered and sorted ascending. Iterate that list in order — never re-derive or re-sort it. (`last_applied_version` may be `0.0.0` for a legacy/unknown install → every manifest plays.)

For each manifest, for each item, run the detector via bash:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||'); cd "$PLUGIN_ROOT"
python3 -c "
import sys, json, importlib
sys.path.insert(0, 'shared/scripts')
mod = importlib.import_module('release_detectors.<detector_module>')
fn = getattr(mod, '<detector_function>')
result = fn('${WORKSPACE}/_hq/data/events.jsonl')
print(json.dumps(result))
"
```

Skip items whose detector returns `{"applies": False}`. For items returning `{"applies": True, "context": {...}}`:

- **action: `announce_only` or `instruct_user`** — format the `prompt_template` with `.format(**detector_context)` and add to `pending_release_remediations` list.
- **action: `auto_apply` (v3.14.4+)** — invoke the action module's function via bash python (same plugin-root resolution as the detector), passing `(events_jsonl_path, workspace_root, detector_context)`. Handle the result per the auto_apply contract in Step 4.8b. See `references/RELEASE_MANIFEST.md` "Action contract" for the full schema.

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||'); cd "$PLUGIN_ROOT"
python3 -c "
import sys, json, importlib
sys.path.insert(0, 'shared/scripts')
mod = importlib.import_module('release_actions.<action_module>')
fn = getattr(mod, '<action_function>')
result = fn('${WORKSPACE}/_hq/data/events.jsonl', '${WORKSPACE}', $DETECTOR_CONTEXT_JSON)
print(json.dumps(result))
"
```

**Idempotency — type-aware (v3.13.8.3+, Bug #73 fix; v3.14.4+ extends to auto_apply):** prior-seen items are filtered based on their `action` type:

- **`action: announce_only` items:** skip if the item's `id` already appears in `applied_remediation_ids` or `skipped_remediation_ids` in any prior `plugin_update` or `plugin_update_remediation` event. Fire-once idempotency — these are informational and don't need re-surfacing.
- **`action: instruct_user` items:** ignore prior-seen state and re-evaluate the detector. If the detector still returns `{"applies": True}` (pending state still exists — e.g., user never named their AI, never picked a workspace shape), re-surface the prompt this run. If the detector now returns `applies: False` (user took the action), it's already filtered out by the detector check above. The detector IS the idempotency check for instruct_user — that's what makes the contract honest: the bridge re-nudges until the action lands or the user explicitly declines.
- **`action: auto_apply` items (v3.14.4+):** rely on the underlying action's own idempotency (typically a `_already_ran` check inside the wrapped helper). On each bridge run: re-evaluate the detector; if applies=True, invoke the action; the action returns `ran=False` if it short-circuited (already-applied) — bridge skips surfacing and does NOT mark applied. If `ran=True`, surface the notice and mark applied. Customers never see a "we already did this" message because the action surfaces only on the first effective run.
- **User-declined items** (`reason: "user_declined_permanently"` in a prior `plugin_update_remediation` event with `decline_kind: "permanent"`): always skip regardless of action type. The user opted out; respect that. They can opt back in by saying `redo release remediations`.

v3.13.8.3 makes the contract state-aware: announce when there's nothing to do, re-nudge when there is (the fire-once regression this fixed is in references/HISTORY.md § Bug #73). v3.14.4 adds auto_apply on top — the system DOES the thing rather than nudging the customer to type a phrase.

### Step 4.8b — Surface to user

If `pending_release_remediations` is empty → silent, proceed to Phase 5.

If non-empty → surface as a single labeled block, plain English, no per-item interaction needed (the prompts themselves carry the action for the user — either announce-only or instruct-user):

> *"**Here's what's worth knowing since you last updated:**"*
>
> Then a numbered list — for each item, the headline of its manifest (deduplicate if multiple items share a manifest), then the formatted prompt indented under it.

Example for a v3.4.1 → v3.4.5 upgrade where the user has 47 dropped commitments:

```
Since v3.4.1 → v3.4.5, here's what's worth knowing:

1. v3.4.2 — Inbound bug-triage skill added.
   v3.4.2 added the `process bug report` skill — receiving-side counterpart of the
   outbound `report-bug`. Next time a customer or teammate forwards you a bug report,
   the operator-side triage flow picks it up from there (customers only ever use `report a bug`): it verifies the diagnosis against the codebase
   with file:line references, runs a same-class sweep for related broken sites, logs
   the bug, and surfaces a fix-scope recommendation. Drafts a voice-calibrated
   acknowledgment reply held in Gmail Drafts (never auto-sent).

2. v3.4.4 — Commitments filter, full shape coverage.
   v3.4.4 fixed a filter that was silently dropping commitments. Your workspace has 47
   open commitment events that should have been surfacing in your daily Commitments
   fire but weren't (breakdown by shape: {'flat-new': 6, 'legacy': 19,
   'owner_person_id-variant': 7, 'other': 15}). Re-fire your Commitments task now to
   see them, by opening the Commitments chat in Cowork and saying `re-run`. Or wait
   for tomorrow's scheduled 8:30 AM fire — same outcome, just a delay.

3. v3.4.5 — Release-manifest system — update command room now plays per-version remediations.
   [prompt body...]
```

No "y/n" follow-up needed. `action: announce_only` items are informational; `action: instruct_user` items tell the user exactly what to type or click (use sparingly — see Rule 28 + RELEASE_MANIFEST.md "Action types"); `action: auto_apply` items (v3.14.4+) show the customer a plain-English notice about something the system already did on their behalf. Default to `auto_apply` for anything the system can resolve without customer input.

**auto_apply per-item handling at surface time:**

For each `auto_apply` item whose detector returned `applies=True`:

1. Invoke the action_module.action_function per the bash pattern above.
2. If `success=True ran=True`: format `notice_template` with `.format(**merged_context)` where `merged_context = {**detector_context, **action_context}`. Add to surface list with action="auto_apply" label so the user sees it's something the system did.
3. If `success=True ran=False`: action was a no-op (already-applied). Do NOT add to surface list. Do NOT mark applied — the detector will handle re-evaluation next run.
4. If `success=False` AND result has `fallback_prompt` AND manifest item has `fallback_prompt_template`: format the fallback template and surface as if instruct_user. Log `release_action_failed_fallback` event with `{error, item_id}`.
5. If `success=False` with no fallback: log `release_action_failed` event silently with `{error, item_id}`. Do NOT surface anything to the customer — a failure they can't act on creates more friction than silent skip.

Sample surface block for an auto_apply item:

```
1. Your activity log: I quietly set aside 12 incomplete entries from old data (recent).
   Your real history is intact.
```

The "I [did the thing]" framing is the canonical voice for auto_apply notices — past tense, customer-visible outcome, plain English. No "type X to do Y" instructions.

### Step 4.8c — Log per-item events

For each item that surfaced (applies=True and not previously seen), append a `plugin_update_remediation` event to events.jsonl (OMIT `seq`/`ts` — the append gate auto-stamps both inside the writer lock, `ts` in UTC; a hand-typed "now" was the F-15 naive-local-clock bug class, v4.5.2 R4.)

```jsonl
{"type":"plugin_update_remediation","source_skill":"command-room-update-bridge","data":{"manifest_version":"3.4.4","item_id":"v344_refire_commitments","action":"instruct_user","detector_context":{"count":47,"by_shape":{...}}}}
```

The item_id is what makes future re-runs idempotent **for `announce_only` items** — the next time `update command room` fires, `announce_only` items with matching `(manifest_version, item_id)` in events.jsonl are filtered out at Step 4.8a. **`instruct_user` items do NOT use prior-seen idempotency** (v3.13.8.3+, Bug #73 fix); they re-surface as long as their detector still returns `applies: True`. The `plugin_update_remediation` event is still written for `instruct_user` items so audit trails capture every surfacing, but it does not gate future surfacings — only the detector + permanent-decline check do.

### Failure handling

- **Manifest JSON malformed:** log `release_manifest_parse_failed` with `{version, error}`, skip that manifest only, continue with the next. Don't block other manifests on one broken one.
- **Detector module import fails:** log `release_detector_import_failed` with `{module, error}`, skip the item, continue.
- **Detector raises:** treat as `{"applies": False}`, log `release_detector_raised` with `{module, function, error}`, skip the item, continue.
- **Detector returns malformed result (missing "applies" key, etc.):** treat as `{"applies": False}`, log `release_detector_malformed_result`, skip.
- **Action module import fails (auto_apply, v3.14.4+):** log `release_action_import_failed` with `{module, error}`. Surface skipped silently OR fall back to instruct_user if `fallback_prompt_template` provided.
- **Action function raises (auto_apply, v3.14.4+):** caught by the action wrapper itself — should return `success=False error=<traceback>`. If the action wrapper itself crashes (not the wrapped logic), log `release_action_raised` and treat as silent skip.

The goal: a broken manifest, detector, or action never blocks the rest of the update flow. The user gets the items that worked; the broken ones land in the failure log for the maintainer.

---

## Phase 5: Log the update event

After installs, workspace migrations, scheduled-task registration, and release-manifest remediations complete (full or partial), append a single event to `_hq/data/events.jsonl`:

```jsonl
{"id":"evt_NNN","timestamp":"<ISO>","type":"plugin_update","from_version":"<INSTALLED>","to_version":"<CURRENT>","installed_artifacts":["orgs-map","quick-commands"],"failed_artifacts":[],"applied_migrations":["prompt_restructuring_preference"],"skipped_migrations":[],"applied_remediation_ids":["v344_refire_commitments","v345_announce_manifest_system"],"actor":"command-room-update-bridge"}
```

If failures occurred, list them in `failed_artifacts`. If migrations were skipped (user-declined or structural mismatch), list them in `skipped_migrations` with a brief reason inline. The `applied_remediation_ids` list carries every release-manifest item the user saw in Phase 4.8 (v3.4.5+). The next run of this skill detects the partial update and offers to retry the failed ones — but does NOT re-prompt for migrations the user explicitly declined (unless they say `redo workspace migrations`).

**Release-manifest idempotency is type-aware (v3.13.8.3+, Bug #73 fix):**
- `announce_only` items in `applied_remediation_ids` → fire-once, not re-surfaced.
- `instruct_user` items in `applied_remediation_ids` → re-evaluated each run via their detector. If the detector still returns `applies: True`, re-surface; once the user takes the action and the detector returns `applies: False`, the item silently stops surfacing.
- Items with a prior `plugin_update_remediation` event carrying `decline_kind: "permanent"` → always skipped regardless of type.

---

## Phase 6: Surface the optional add-ons (one line, no pitch)

After successful install, end with one line. Render it from the ADDONS set — never a hardcoded count:

- If ADDONS is empty (current state as of v3.11.0):

> *"You're up to date with both default dashboards installed. You're done."*

- If ADDONS is non-empty at your version:

> *"You're up to date with both default dashboards installed. Optional add-ons are available — say `level up command room` to see them. Otherwise, you're done."*

Don't list the add-ons inline. The user can discover them at their pace.

---

## What this skill does NOT do

- Does not run schema migrations. v2.7.9 doesn't introduce schema changes; if a future version does, that's a separate skill (`command-room-migrate`).
- Does not auto-uninstall deprecated artifacts. Command Atlas / Commitment Cockpit / Pay Attention To stay if the user has them pinned. They're harmless.
- Does not modify `entities.json`, `aliases.json`, or any user data file (people-crm and workspace-manager remain canonical owners).
- Does not modify skill files. Skill behavior updates flow automatically through Anthropic's plugin distribution — this skill only fills the gap for things distribution can't reach (artifact installs and workspace-folder file edits).
- Does not re-run onboarding. If the user wants a full reset, they say `restart onboarding` (different skill).
- Does not install opt-in add-ons (Commitment Cockpit, Pay Attention To, Meeting Processor) — those go through `level up command room` or their direct `enable-*` triggers.
- Does not retry failed installs automatically. Surfaces the failure once; user retries manually.
- Does not re-prompt for migrations the user explicitly declined. If they said "No" to prompt restructuring on first run, subsequent runs respect that choice unless they say `redo workspace migrations`.
- Does not run on every session start. Only fires on explicit user trigger.

---

## Edge cases

**No `events.jsonl` exists.** User is on a very old install that pre-dates the JSON substrate. Route them to `restart onboarding` instead — update-bridge can't help; they need a full re-run.

**Plugin version JSON is unreadable.** Treat `from_version` as `"unknown"` and assume all v2.7.9 defaults are missing. Proceed with full install.

**User has more recent artifacts installed than the v2.7.9 set knows about.** They're on a newer version than this skill expects. Tell them: *"Your version is ahead of what this skill knows about. No action needed."*

**`enable-*` skill not present in plugin.** Skip that one in the install loop, mark it as failed in `plugin_update.failed_artifacts`. User probably has a partial plugin; no clean recovery path from this skill.

**CLAUDE.md exists but `## Preferences` heading is missing.** Pre-v2.4 workspaces may have a different structure. Skip the workspace-migration with `reason: "structural_mismatch"`. Tell the user: *"Your CLAUDE.md is missing the Preferences section — that's a bigger change than I can safely make on my own. Say `restart onboarding` if you want a clean rebuild, or skip this for now."* Don't try to recover automatically.

**User declined a workspace migration on a prior run.** Detection sees the prior `workspace_migration_skipped` event with `reason: "user_declined"`. Skip in the candidate list. The migration is removed from `pending_workspace_migrations` for this run. The user can opt back in by saying `redo workspace migrations` (which clears the skip events and re-runs Phase 4.5).

**Workspace migration partially applied.** If a prior run logged `workspace_migration_applied` but the marker check fails (user manually edited or removed the line afterward), treat as pending and re-prompt. Don't silently re-apply — the user clearly modified something on purpose, so confirm before re-adding.

**`create_artifact` returns success but Rule 8 verification fails. (Added v2.7.10.)** This means the tool accepted the call but what landed in Cowork doesn't match the canonical template — possible causes: tool truncation, encoding mishandling on the wire, payload limit silently clipped the input. Mark the install failed via `artifact_install_failed` with `reason: "verification_failed:<which>"`. Surface to user: *"Workspace Map installed but didn't pass my check — what landed doesn't match the original. Don't pin it yet. Restart Cowork and say `update command room` to retry, or report the problem if it keeps happening."* Do NOT pin, do NOT log `artifact_installed`, do NOT continue to the next artifact in the batch.

**Prior install of a non-canonical "compact equivalent" exists from pre-v2.7.10. (Added v2.7.10.)** Some users (e.g., Dustin Sample's install on 2026-04-26) received a hand-rolled improvised artifact from the v2.7.9 bridge before the Rule 7 + Rule 8 enforcement was in place. Detection: an `artifact_installed` event exists for `workspace_map` or `daily_command_center` but the live artifact fails the Rule 8 verification block (size ≪ 80% of source, or missing the marker string, or contains `â€` mojibake). Action: append an `artifact_install_failed` event with `reason: "non_canonical_predecessor"`, surface to user: *"You have a Workspace Map installed, but it's not the right one — an earlier version installed a broken copy. I'd like to remove it and install the real one. OK to proceed? (yes / no)"* On yes, uninstall the non-canonical artifact (call `mcp__cowork__delete_artifact` with the installed artifact id if the tool exists in this session; otherwise tell the user: *"Right-click the Workspace Map in your Cowork sidebar and choose Remove, then say 'update command room' and I'll install the real one."*) and re-run the install. On no, leave it alone but flag in the next `cleanup`.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Installs the two Layer 1 default sidebar dashboards (Orgs Map, Quick Commands) and applies any pending workspace-level migrations (CLAUDE.md preference additions, BUSINESS_CONTEXT additions). Serves both fresh-onboarded users (onboarding defers dashboard installs to this skill so the demo stays fast) and existing-version upgrade users (detects which of the defaults are already installed; only installs the missing ones). Idempotent — re-runs are safe. Triggers on 'install my dashboards', 'install dashboards', 'install missing dashboards', 'i'm missing dashboards', 'set up my dashboards', 'add my dashboards', 'update command room', 'update my command room', 'whats new in command room', "what's new in command room", 'install latest', 'install the latest', 'check for updates', 'redo workspace migrations' (clears prior skip events and re-runs the migration pass). DOES NOT fire on a bare what's-new greeting with no Command Room in the sentence (small talk — answer conversationally, never run a version check). DOES NOT fire on 'level up command room' (umbrella for opt-in add-ons), 'rebuild [artifact]' (refresh, not install), 'restart onboarding' (full re-run, separate skill).
