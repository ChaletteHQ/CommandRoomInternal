# Orchestrator prompt — Commitment Triage

This file is the EXACT prompt the bootloader cats and executes for `taskId: commitment-triage`. Fires 3:00 PM Friday local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES` (`0 15 * * 5`) — AFTER friday-wrap (13:00), so the wrap reads the week as it was and the triage cleans the open set before the weekend. NEW in Phase 2 Stage D (S4). **NOT a first-install task** — a fresh workspace's open set hasn't aged; it registers via `change-schedule` / Phase 6 `add` and `command-room-update-bridge`, never on a fresh workspace.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** (transport-updated EW2+T) every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. Commitment Triage is a **widget action surface**: post via `widget_transport.render_and_persist` — the full validator chain (canonical verbs per `CANONICAL_ACTIONS` — the triage verb set IS canonical as of Stage D; data shape; leak scan: no entity-ID leaks, no event-type names, no `_hq/` paths; `validate_rendered_widget`) runs inside the one call — then pass `transport["html"]` (the persisted page's validated bytes, verbatim) to `mcp__visualize__show_widget` as `widget_code`, never hand-composed HTML (`shared/CHAT_ACTION_WIDGET.md` § Transport, F-15).

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface the link block per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" only if files were produced (usually none). The widget is the ENTIRE chat turn.

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for the existing `skills/commitment-triage/SKILL.md`. That skill is the source of truth for the projected-open-set load, the age sort, the stale-task annotation, the widget shape, and the undo contract. This orchestrator's job is to (a) resolve plugin + workspace paths, (b) execute the commitment-triage skill's Steps 1–4 verbatim, (c) ensure the resulting widget posts to chat once, (d) log a `pack_run` event, (e) STOP.

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

**The widget IS the chat turn. After it posts, YOU STOP.** No exceptions. Applies to first fires AND re-runs.

**Forbidden — zero tolerance:**

1. **No writing the rendered widget to disk by hand.** (The transport's own persist into `_hq/.system/widgets/` is the ONE sanctioned write — `render_and_persist` performs it itself, per `shared/STOP_CONTRACT.md` rule 1. You never write widget HTML anywhere else, and never offer the persisted file to the user as a deliverable.)
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

- Today's date is `clock["today"]` from the Phase 2.9 return (CLOCK1) — the corroborated instant, already expressed in the workspace timezone by code. Never compute it from this computer's clock: an unsynced sandbox clock reading two days behind is what surfaced a meeting that had already happened as upcoming. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` exactly as before (REQUIRED `workspace_path`; on `TZResolutionError`, proceed with UTC and note it).
- Read `<WORKSPACE>/_hq/data/entities.json` — primary user + people (for owner display names).
- No connector reads — this surface is pure substrate.

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire — a typed trigger, a Run Now click, a re-run request in an open chat. **When uncertain, it is `manual`**: a mis-labeled manual costs one missing lateness note; a mis-labeled scheduled fabricates lateness history (FINDINGS F-47 P1a — three false late_fire receipts in one afternoon).

Cowork fires a missed slot at next app launch, hours or days late, and without this check the run would render a stale surface as if it were fresh. Compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local, the clock cron actually evaluates in), passing the detected run mode:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'commitment-triage', fired_via='<scheduled|manual>', env_date='<session date>')))
"
```

**Every python subprocess in this fire carries `CR_WORKSPACE` (CLOCK1).** Prefix them: `CR_WORKSPACE=<WORKSPACE> python3 -c "..."`. Each `python3 -c` is its own process started from the plugin root, so a helper left to guess which workspace it is in finds nothing, cannot cross-check the clock, and stamps whatever this computer says. The phases that run BEFORE the lateness check write to the ledger too, which is exactly where an unchecked clock does its permanent damage.

**Pass the session date too (CLOCK1).** `env_date` is this session's own date — the `Today's date is YYYY-MM-DD` line in your context. It is the second source the run cross-checks this computer's clock against, and the only one that can catch a clock running fast. Substitute the date and nothing else; if you genuinely do not have one, pass an empty string. A value that is not a date is treated as absent: it never moves the clock and never blocks the fire.

**The clock verdict comes back as `clock`, and two things follow from it. Neither is optional:**

- **When `clock["notice"]` is set, it is the FIRST line of this fire's output** — above the lateness banner, verbatim, never paraphrased and never dropped. It states that the dates in this surface came from the workspace record rather than this computer's clock. A silent substitution is its own bug: the reader has no other way to know which clock produced what they are looking at.
- **Today's date is `clock["today"]`** — take it from the return rather than computing one here.


Branch on `tier` (this does not weaken the anti-improvisation contract — every phase below still executes verbatim; the tier only governs what is RENDERED):

- **`manual`** — an interactive fire is never late: run EVERY phase normally (connector pre-scans included — a run mode never adds skip conditions), with NO timing banner and NO lateness narrative of any kind, anywhere. The helper wrote no event; do not hand-compute lateness around it (FINDINGS F-47 P1a).
- **`none` / `exempt` / `unknown`** — run normally. No mention of timing anywhere. `none` with a `suppressed` reason means the helper's ledger found the slot already served (a receipt exists after it) or minted by a schedule change — believe it: never re-derive lateness, never invent a cause ("the computer was probably asleep").
- **`note` (3–24h late)** — run ALL phases normally, but the chat output OPENS with the returned `banner` line verbatim (one line, before anything else). Nothing else changes.
- **`degrade` (>24h late)** — the triage set is computed from the live substrate, so unlike digest surfaces it does NOT go stale; still, per the shared contract, post the returned `banner`-equivalent context by running normally on `note`, and on `degrade` post ONLY the returned `degrade_notice` line and STOP after the Phase-final `pack_run` (the next Friday fire carries the triage; nothing is lost — the open set persists in events.jsonl).

The helper already appended the `late_fire` telemetry on note/degrade tiers — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

# Phase 3 — Execute the commitment-triage skill verbatim

Run `skills/commitment-triage/SKILL.md` Steps 1–4 against this workspace:

1. Load the PROJECTED open set via `cru_match.load_open_commitments` (deferrals + reclassification markers already applied); header counts via `commitment_state.count_commitments` (the one counting API); stale tasks via `commitment_state.stale_tasks`.
2. Sort by age, oldest first into the FULL-LIST layout (header stat tiles from `counts["headline"]` via `counters`, `30+ DAYS OLD` section, `reduced_verbs_reason` on pending_review rows) — the one triage design per the skill's Step 2 (F-18). The transport carries any size — there is no transmission ceiling and no chunking (EW2+T; `show more` pagination remains a DESIGN choice per the skill's Step 2, never a size workaround). Annotate stale tasks with "still on your plate?". SUB1 families (the skill's § Sub-items) render nested — sub-item rows inside their parent, progress chip on the parent, pagination family-atomic; the driver builds all of this, never re-derive.
3. Render ONE `all_batch_widget` with the triage verb set (every row embeds `data.id` VERBATIM; `source_skill: "commitment-triage"` for `src` dispatch) and post it via `render_and_persist` → `show_widget` with `transport["html"]` as `widget_code` per the OUTPUT CONTRACT above.
4. Dispatch happens later via apply-choices § `commitment-triage` — including the `undo` contract.

# Phase 4 — Audit + STOP

Append the fire receipt via the canonical helper (`shared/scripts/receipts.py`, v4.5.2 R1 — **NEVER hand-roll the receipt JSON**, and never skip it: the dogfood caught this task writing a receipt one day and zero events the next, FINDINGS F-56): `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "commitment-triage", fired_via=<the Phase 2.9 receipt_fired_via: manual|scheduled|catchup>, surfaced=open_set_total, extra_data={"stale_tasks": stale_count})` — `receipt_fired_via` is what Phase 2.9's helper returned, never guessed. Then STOP — the widget is the whole turn.
