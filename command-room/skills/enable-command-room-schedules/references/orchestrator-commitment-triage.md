# Orchestrator prompt — Commitment Triage

This file is the EXACT prompt the bootloader cats and executes for `taskId: commitment-triage`. Fires 3:00 PM Friday local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES` (`0 15 * * 5`) — AFTER friday-wrap (13:00), so the wrap reads the week as it was and the triage cleans the open set before the weekend. NEW in Phase 2 Stage D (S4). **NOT a first-install task** — a fresh workspace's open set hasn't aged; it registers via `change-schedule` / Phase 6 `add` and `command-room-update-bridge`, never on a fresh workspace.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. Commitment Triage is a **widget action surface**, so the renderer-validator gates DO apply: `render_chat_output_widget` + `validate_rendered_widget` must pass with zero non-canonical verbs (the triage verb set IS canonical as of Stage D — see `CANONICAL_ACTIONS`). The leak scanner applies (no entity-ID leaks, no event-type names, no `_hq/` paths).

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface the link block per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" only if files were produced (usually none). The widget is the ENTIRE chat turn.

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for the existing `skills/commitment-triage/SKILL.md`. That skill is the source of truth for the projected-open-set load, the age sort, the stale-task annotation, the widget shape, and the undo contract. This orchestrator's job is to (a) resolve plugin + workspace paths, (b) execute the commitment-triage skill's Steps 1–4 verbatim, (c) ensure the resulting widget posts to chat once, (d) log a `pack_run` event, (e) STOP.

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

**The widget IS the chat turn. After it posts, YOU STOP.** No exceptions. Applies to first fires AND re-runs.

**Forbidden — zero tolerance:**

1. **No writing the rendered widget to disk.**
2. **No narrating what's in the widget.** The user can see the rows. Don't follow with "You have N stale items…".
3. **No post-widget summary block.** The turn ends after the widget.
4. **No closing anything at render time.** Every closure/defer/reclassify is a user click dispatched through apply-choices → `commitment_state`. Render-time writes are the F4 mutation class this surface exists to kill.
5. **No in-place edits to events.jsonl. Ever.** All triage writes are appends through `commitment_state` helpers.

**Self-check before posting anything after the widget:** "is this required by spec?" If no → don't post it.

---

You are firing the Command Room "Commitment Triage" chat. Today is Friday in workspace LOCAL time. You're rendering the FULL open commitment set, oldest first, so the CEO can burn down rot in one sitting before the weekend — done / defer / drop / not mine / make task / promote / never-track, one Apply, undo available. DELIBERATE EXEMPTION: unlike the daily widget orchestrators, this surface does NOT filter against the learned suppression store (_hq/data/surface-preferences.json) — it is the full-set audit where the CEO reviews everything, including items other surfaces have learned to mute; 'never-track' rules are WRITTEN here, so hiding already-muted items would make them unreviewable. Do not 'fix' this by adding the filter.

# Phase 1 — Always run (no idempotency gate)

This orchestrator ALWAYS runs when fired — by cron or manual `triage my commitments` trigger. A `pack_run` event writes at the end of every fire for audit trail. Re-fires are safe: closed items simply don't load, and `close_commitment` is idempotent over the full resolved-id set.

# Phase 2 — Setup

The bootloader already resolved `PLUGIN_ROOT`, `WORKSPACE`, and this orchestrator file path. Continue with:

- Compute today's date in local time via `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` (REQUIRED `workspace_path`; on `TZResolutionError`, proceed with UTC and note it).
- Read `<WORKSPACE>/_hq/data/entities.json` — primary user + people (for owner display names).
- No connector reads — this surface is pure substrate.

# Phase 2.9 — Lateness check (Phase 3 / R4 — runs BEFORE any surface is rendered)

Cowork fires a missed slot at next app launch, hours or days late, and without this check the run would render a stale surface as if it were fresh. Compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local, the clock cron actually evaluates in):

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'commitment-triage')))
"
```

Branch on `tier` (this does not weaken the anti-improvisation contract — every phase below still executes verbatim; the tier only governs what is RENDERED):

- **`none` / `exempt` / `unknown`** — run normally. No mention of timing anywhere.
- **`note` (3–24h late)** — run ALL phases normally, but the chat output OPENS with the returned `banner` line verbatim (one line, before anything else). Nothing else changes.
- **`degrade` (>24h late)** — the triage set is computed from the live substrate, so unlike digest surfaces it does NOT go stale; still, per the shared contract, post the returned `banner`-equivalent context by running normally on `note`, and on `degrade` post ONLY the returned `degrade_notice` line and STOP after the Phase-final `pack_run` (the next Friday fire carries the triage; nothing is lost — the open set persists in events.jsonl).

The helper already appended the `late_fire` telemetry on note/degrade tiers — do not append a second one, and never narrate the event or the tier name to the user.

# Phase 3 — Execute the commitment-triage skill verbatim

Run `skills/commitment-triage/SKILL.md` Steps 1–4 against this workspace:

1. Load the PROJECTED open set via `cru_match.load_open_commitments` (deferrals + reclassification markers already applied); header counts via `commitment_state.count_commitments` (the one counting API); stale tasks via `commitment_state.stale_tasks`.
2. Sort by age, oldest first; paginate at the transmission ceiling with `show more`; annotate stale tasks with "still on your plate?"; `pending_review` rows per the skill's Step 2.
3. Render ONE `all_batch_widget` with the triage verb set (every row embeds `data.id` VERBATIM; `source_skill: "commitment-triage"` for `src` dispatch).
4. Dispatch happens later via apply-choices § `commitment-triage` — including the `undo` contract.

# Phase 4 — Audit + STOP

Append one `pack_run` event to `_hq/data/events.jsonl` via `atomic_append_jsonl` with `data.task_id: "commitment-triage"`, the open-set total, and the stale-task count. Then STOP — the widget is the whole turn.
