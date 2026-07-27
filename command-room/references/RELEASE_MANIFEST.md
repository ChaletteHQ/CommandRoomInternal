# Release Manifest contract (v3.4.5+)

Every Command Room release ships a manifest at `shared/releases/v<X.Y.Z>.json` declaring what the `command-room-update-bridge` skill should do for users coming from prior versions. Mandatory — `ship-cr-plugin` blocks on its presence (no manifest, no ship).

The manifest exists because plugin code updates only fix forward — they don't reach existing workspace state. A user on v3.4.1 who upgrades to v3.4.5 gets the new code, but:

- Past events written in shapes the new consumer logic now handles still need to be *surfaced* (a one-time re-fire prompt closes that gap).
- New skills they should know about don't auto-discover themselves — they need an announcement.
- Workspace files (CLAUDE.md, BUSINESS_CONTEXT) that grew new required sections need a migration pass.
- Default dashboards that were added need to be installed.

Each manifest enumerates these and the update-bridge plays them in order.

## Schema

```json
{
  "version": "3.4.4",
  "headline": "Short, user-facing one-liner shown above the items list",
  "items": [
    {
      "id": "v344_refire_commitments",
      "detector_module": "release_detectors.v3_4_4_dropped_commitments",
      "detector_function": "count_dropped_open_commitments",
      "prompt_template": "v3.4.4 fixed a filter that was silently dropping commitments. Your workspace has {count} open commitment events that should have been surfacing but weren't (breakdown: {by_shape}). Re-fire your Commitments task now to see them, or wait for tomorrow's scheduled 8:30 AM fire.",
      "action": "instruct_user"
    }
  ]
}
```

### Field-by-field

**Top-level:**

| Field | Required | Notes |
|---|---|---|
| `version` | yes | Must match the plugin.json version this manifest ships in. Used by the bridge to advance the "last applied" pointer. |
| `headline` | yes | One line user-facing summary. Shown above the items list. Plain English; no file paths, no rule numbers, no dev-internal noise (per the existing Rule 9 in `command-room-update-bridge/SKILL.md`). Guard-enforced: the jargon guard scans this field, and because it carries no `action` it gets both the word-level and the plumbing-instruction rule sets — a headline never tells the customer to type a phrase. |
| `items` | yes | List of remediation items (may be empty for pure-internal releases that need no user surface). |

**Item:**

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Stable identifier. Used in the `plugin_update_remediation` events.jsonl event so re-runs of the same manifest don't re-prompt for items the user already acted on. Convention: `v<X.Y.Z minor>_<short_slug>`. |
| `detector_module` | yes | Python module under `shared/scripts/release_detectors/`. Imported via `from release_detectors.<module> import ...`. Use `release_detectors.always` for items that should always show to users coming from a prior version. |
| `detector_function` | yes | Function name inside the module. Signature: `def fn(events_jsonl_path: str) -> dict`. Returns `{"applies": bool, "context": {key: value, ...}}`. The context dict provides values for `prompt_template` substitution. |
| `prompt_template` | yes | User-facing prompt shown when the detector returns `applies: True`. Uses Python `.format(**context)` — every placeholder must be a key in the detector's returned `context` dict (or `{count}`/`{by_shape}` etc. that the detector always returns). Plain English; no dev-internal noise. |
| `action` | yes | One of `instruct_user`, `announce_only`, or `auto_apply` (v3.14.4+). See the per-action contracts below. |
| `action_module` | only for `auto_apply` | Python module under `shared/scripts/release_actions/`. Imported via `from release_actions.<module> import ...`. |
| `action_function` | only for `auto_apply` | Function name inside the action_module. Signature: `def fn(events_jsonl_path, workspace_root, detector_context) -> dict`. See the action contract below. |
| `notice_template` | only for `auto_apply` | Plain-English notice shown to the user AFTER the action runs (replaces `prompt_template` for `auto_apply` items). Formatted with `.format(**merged_context)` where `merged_context = {**detector_context, **action_context}`. |
| `fallback_prompt_template` | optional, only for `auto_apply` | Used if the action fails AND returns `fallback_prompt` in its result. Falls back to surfacing as an `instruct_user`-style prompt. If omitted, action failures are logged silently with no user surface. |

## Action types

**`announce_only`** (v3.4.5+) — informational. Detector decides whether to surface; the prompt itself is the entire surface. No execution. Use for: new-skill announcements, "we tightened X under the hood" notes, anything the customer should know but doesn't need to act on.

**`instruct_user`** (v3.4.5+) — the prompt itself tells the customer what to type or click. No skill-side execution. Use ONLY for actions that genuinely need customer input the system can't provide (assistant name, workspace shape choice, opt-in/out decisions). Default to `auto_apply` first; reach for `instruct_user` only when you've ruled it out.

**`auto_apply`** (v3.14.4+) — the bridge runs the action silently and surfaces a plain-English notice about what it did. Use for: substrate hygiene, default-registration of new chats/skills, additive backfills, anything where the system can pick the right answer and the customer doesn't need to be involved in the decision.

Safety constraint on `auto_apply`: actions MUST be additive, reversible, and no-data-loss. Substrate-rewriting actions (corruption recovery, backfills) are allowed because they quarantine sidecar-style — original data is preserved. Anything destructive (delete, overwrite without backup) MUST stay `instruct_user` so the customer explicitly consents.

The non-technical-customer principle (CONTRACT.md Rule 28): if the question would expose schema / migration / JSON / `taskId` / `enum` / `events.jsonl` / `entities.json` / `backfill` / `quarantine` / `re-fire` / `re-register` / `wrapper` vocabulary to the customer, it MUST be `auto_apply` (the system picks the answer) or rewritten in plain English. The jargon-guard pre-commit test scans customer-facing manifest surfaces — the top-level `headline` plus each item's `prompt_template`, `notice_template` and `fallback_prompt_template` — for these patterns and fails the ship if any leak. (Quoted customer prose in SKILL.md files is covered by a sibling guard, `tests/run_customer_facing_voice_test.py`, against its own rule set — not by this one.)

## Detector contract

A detector function lives at `shared/scripts/release_detectors/<module>.py` and exports one or more named functions matching the `detector_function` field in manifest items. Each function:

- Takes a single argument: the absolute path to the user's `_hq/data/events.jsonl`.
- Returns a dict: `{"applies": bool, "context": dict}`.
- Is pure-read — must NOT mutate workspace state. The update-bridge calls it to *decide whether to surface a prompt*; mutation only happens on user confirmation, and the action is whatever the prompt instructs.
- Should be idempotent under re-runs and degrade gracefully (return `{"applies": False, "context": {}}`) when the events file doesn't exist or is unreadable, rather than raising.
- Is also CLI-invokable (`if __name__ == "__main__": ...`) so the update-bridge skill can shell out via `bash python <module>.py <events_path>` and parse the JSON result, avoiding a full Python import chain.

See `release_detectors/v3_4_4_dropped_commitments.py` for the canonical example, and `release_detectors/always.py` for the trivial always-applies detector used by `announce_only` items.

## Action contract (auto_apply, v3.14.4+)

An action function lives at `shared/scripts/release_actions/<module>.py` and exports a function matching the `action_function` field in the manifest item. Signature:

```python
def fn(events_jsonl_path, workspace_root, detector_context) -> dict:
    '''
    Args:
        events_jsonl_path: Path to _hq/data/events.jsonl.
        workspace_root: Path to the workspace root (parent of _hq/).
        detector_context: dict returned by the detector for this item.

    Returns:
        {
          "success": bool,             # action completed without raising
          "ran": bool,                 # True if it did work; False if no-op
          "context": dict,             # merged into detector_context for notice_template
          "error": Optional[str],      # set when success=False
          "fallback_prompt": Optional[str]  # if success=False AND we want to fall back
                                            # to instruct_user-style prompt
        }
    '''
```

**Idempotency** — actions MUST short-circuit when their effect is already applied (typically by checking for a prior event in events.jsonl, or by deferring to an existing `run_X_if_needed` helper that already checks). Return `success=True ran=False` in that case so the bridge skips surfacing a notice.

**Failure handling** — if the action raises or returns `success=False`:
- If the action returned `fallback_prompt` text AND the manifest item provides `fallback_prompt_template`: bridge surfaces it as if the item were `instruct_user`. Logs `release_action_failed_fallback` event.
- Otherwise: bridge logs `release_action_failed` event silently and does NOT surface anything. The customer experience is "the thing just didn't happen this time" — better than a confusing prompt about something they don't understand.

See `release_actions/v3_13_8_quarantine_substrate.py` for the canonical example (wraps an existing idempotent helper) and `release_actions/__init__.py` for the contract docstring.

## What the update-bridge does with manifests

Per the post-v3.4.5 phases in `skills/command-room-update-bridge/SKILL.md`:

1. Determine the user's `last_applied_version` from the most recent `plugin_update` event in events.jsonl. If none exists, fall back to `last_onboarding_complete` version. If neither exists, treat as `0.0.0`.
2. Enumerate all `shared/releases/v*.json` files. Filter to versions `> last_applied AND <= current_plugin_version`. Sort ascending.
3. For each manifest in order:
   - For each item, run its detector against the user's events.jsonl.
   - Skip items whose detector returns `{"applies": False}`.
   - For items whose detector returns `{"applies": True}`, format the `prompt_template` against the detector's `context` dict and add to the surface list.
4. Show the surface list to the user (single message, items numbered). Wait for confirmation.
5. For items with `action: "announce_only"`, no execution — the prompt itself is the surface. Mark applied.
6. For items with `action: "instruct_user"`, the prompt itself tells the user the next step. No skill-side execution. Mark applied.
7. For items with `action: "auto_apply"` (v3.14.4+), the bridge imports the action_module + action_function, invokes it with `(events_jsonl_path, workspace_root, detector_context)`, and:
   - If `success=True ran=True`: format `notice_template` with merged context, surface to user, mark applied.
   - If `success=True ran=False`: action was a no-op (already-applied). Skip surfacing; do NOT mark applied (re-detector on next run will catch it if state changes).
   - If `success=False` AND action returned `fallback_prompt`: format `fallback_prompt_template` and surface as instruct_user. Log `release_action_failed_fallback` event.
   - If `success=False` with no fallback: log `release_action_failed` event silently. No user surface.
8. Write a single `plugin_update` event with `data: {version: <current>, applied_remediation_ids: [...], skipped_remediation_ids: [...]}`. The remediation-id lists make re-runs idempotent — items the user already saw won't re-prompt.

## Auto-application landed in v3.14.4

The MVP shipping in v3.4.5 only supported `instruct_user` and `announce_only`. v3.14.4 added `auto_apply` (per the non-technical-customer principle in CONTRACT.md Rule 28) and migrated the pre-existing items that were inappropriately `instruct_user`:

| Item | Pre-v3.14.4 | Post-v3.14.4 |
|---|---|---|
| v3138_substrate_corruption_recovery | instruct_user ("type `run recovery`") | auto_apply via `v3_13_8_quarantine_substrate.auto_quarantine_malformed` |
| v31381_wrapper_source_seq_backfill | instruct_user ("say `run wrapper backfill`") | auto_apply via `v3_13_8_1_run_wrapper_backfill.auto_run_wrapper_backfill` |
| v344_refire_commitments | instruct_user ("re-fire your Commitments task") | announce_only (rewritten prompt — next scheduled fire surfaces them automatically) |
| v3143_friday_wrap_missing | instruct_user ("type `set up command room schedules`") | REMOVED from manifest — detection moved to update-bridge Phase 4.7 silent-add per the M1 inbox-add precedent |

`instruct_user` is no longer the safe default. Default to `auto_apply` first; reach for `instruct_user` only when the action genuinely needs customer input. The remaining `instruct_user` items in shipped manifests (brain_name_prompt, workspace_shape_question) genuinely need the customer's choice — they are not jargon-y migrations.

Other planned actions like `apply_workspace_migration` (auto-append to CLAUDE.md) and `install_artifact` (auto-pin a sidebar dashboard) can be added as needed using the same action-module pattern.

## Constraints on `prompt_template` content

Plain user-facing English. The same Rule 9 that governs the existing update-bridge applies:

- ❌ Don't reference plugin versions in dev terms ("v3.4.4 cru_match.py line 320 fix").
- ❌ Don't reference file paths, function names, rule numbers, schema-doc sections.
- ❌ Don't dump CHANGELOG content. The headline + prompt is the entire user surface.
- ✅ Do say what the user has, what's different now, and what (if anything) to do next.
- ✅ Do use detector context (counts, shapes, etc.) in placeholders — concreteness over abstraction.

## When to skip writing a manifest item

- Pure-internal refactors with no user-observable effect → ship a manifest with empty `items` array. Still required so `last_applied` advances.
- Bug fixes where the fix takes effect on the next scheduled fire with zero user action and the user doesn't need to know → empty items.
- Bug fixes where the user has accumulated state that should now surface (Sam-class) → write an `instruct_user` item with a detector that counts the recovered surface.
- New skills users should discover → write an `announce_only` item using the `always_applies` detector.
- Workspace-file migrations → write an `instruct_user` item for now; once the `apply_workspace_migration` action lands, swap to it.
