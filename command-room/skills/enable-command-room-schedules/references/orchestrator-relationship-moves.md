# Orchestrator prompt — Relationship Moves

This file is the EXACT prompt the bootloader cats and executes for `taskId: relationship-moves`. Fires 5:00 PM Sunday local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES` (`0 17 * * 0`) — the output waits for the CEO Monday morning, an hour before the silent `cleanup` task. NEW in REL1. **NOT a first-install task** — it needs accumulated substrate for dormancy baselines, so it registers via `change-schedule` / Phase 6 `add` and `command-room-update-bridge`, never on a fresh workspace.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** (transport-updated EW2+T) every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. Relationship Moves is a **widget action surface** (email-shaped items with send / edit then send / draft / skip): post via `widget_transport.render_and_persist` — the full validator chain (canonical verbs, email-shaped `metadata` on every item, leak scan: no entity-ID leaks, no internal phase labels, no `_hq/` paths, `validate_rendered_widget`) runs inside the one call — then pass `transport["html"]` (the persisted page's validated bytes, verbatim) to `mcp__visualize__show_widget` as `widget_code`, never hand-composed HTML (`shared/CHAT_ACTION_WIDGET.md` § Transport, F-15).

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface the link block per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section". The widget is the ENTIRE chat turn.

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for the existing `skills/relationship-moves/SKILL.md`. That skill is the source of truth for the ranking math (`shared/scripts/relationship_moves.py`), the live-check gate, the email-writer opener chain, the widget shape, and the dedupe rules. This orchestrator's job is to (a) resolve plugin + workspace paths, (b) execute the relationship-moves skill's behavior verbatim, (c) ensure the resulting widget posts to chat once, (d) log a `pack_run` event, (e) STOP.

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

**The widget IS the chat turn. After it posts (plus the post-widget Links section), YOU STOP.** No exceptions. Applies to first fires AND re-runs.

**Forbidden — zero tolerance:**

1. **No writing the rendered widget to disk by hand** — the transport's own persist into `_hq/.system/widgets/` (performed by `render_and_persist` itself, per `shared/STOP_CONTRACT.md` rule 1) and any canonical artifact path the skill defines are the only sanctioned writes.
2. **No narrating what's in the widget.** The user can see the candidates. Don't follow with "Surfaced N people…" / "Scores were…".
3. **No post-widget summary block.** The turn ends after the widget + Links section.
4. **No padding to 3 candidates.** If the skill returns fewer, render fewer; if zero, render the `all_clear_summary` data view — never hand-built HTML, never invented candidates.
5. **No auto-send.** `send` is always a user click dispatched through apply-choices → email-writer.

**Self-check before posting anything after the widget:** "is this required by spec?" If no → don't post it.

---

You are firing the Command Room "Relationship Moves" chat. Today is Sunday in workspace LOCAL time. You're surfacing the top 3 people worth reaching out to this week, each with a pre-drafted opener, so the CEO starts Monday with the outreach already drafted.

# Phase 1 — Always run (no idempotency gate)

This orchestrator ALWAYS runs when fired — by cron or manual `relationship moves` / `who should I reach out to` trigger. A `pack_run` event writes at the end of every fire for audit trail. The skill's own 7-day dedupe (no double-suggesting someone already emailed/suggested this week) makes re-fires safe.

# Phase 2 — Setup

The bootloader already resolved `PLUGIN_ROOT`, `WORKSPACE`, and this orchestrator file path. Continue with:

- Compute today's date in local time via `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` (REQUIRED `workspace_path`; on `TZResolutionError`, proceed with UTC and note it).
- Read `<WORKSPACE>/_hq/data/entities.json` — primary user, people, emails, relationship tiers.
- Read `<WORKSPACE>/CLAUDE.md` if present (hot cache for people/projects/terms).
- Discover connectors for the live-contact check (Mail native MCP — never Zapier for reads).

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire — a typed trigger, a Run Now click, a re-run request in an open chat. **When uncertain, it is `manual`**: a mis-labeled manual costs one missing lateness note; a mis-labeled scheduled fabricates lateness history (FINDINGS F-47 P1a — three false late_fire receipts in one afternoon).

Cowork fires a missed slot at next app launch, hours or days late, and without this check the run would render a stale surface as if it were fresh. Compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local, the clock cron actually evaluates in), passing the detected run mode:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'relationship-moves', fired_via='<scheduled|manual>')))
"
```

Branch on `tier` (this does not weaken the anti-improvisation contract — every phase below still executes verbatim; the tier only governs what is RENDERED):

- **`manual`** — an interactive fire is never late: run EVERY phase normally (connector pre-scans included — a run mode never adds skip conditions), with NO timing banner and NO lateness narrative of any kind, anywhere. The helper wrote no event; do not hand-compute lateness around it (FINDINGS F-47 P1a).
- **`none` / `exempt` / `unknown`** — run normally. No mention of timing anywhere. `none` with a `suppressed` reason means the helper's ledger found the slot already served (a receipt exists after it) or minted by a schedule change — believe it: never re-derive lateness, never invent a cause ("the computer was probably asleep").
- **`note` (3–24h late)** — run ALL phases normally, but the chat output OPENS with the returned `banner` line verbatim (one line, before anything else). Nothing else changes.
- **`degrade` (>24h late)** — the surface is stale; do NOT render it. Execute every phase below EXCEPT the surface-rendering one (the widget-render/post phase): all substrate writes the task owes — events, view updates, the Phase-final `pack_run` receipt — still happen, silently and explicitly (skipping them is the Bug #98 class: an invisible write must not lose to a suppressed deliverable). Then post ONLY the returned `degrade_notice` line as the entire chat output and STOP. No widget, no digest, no Links section. The next Morning Brief reads events.jsonl, so nothing captured is lost.

The helper already appended the `late_fire` telemetry on note/degrade tiers (cleanup and the insight pass consume it to propose better default times) — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

# Phase 3 — Execute the relationship-moves skill verbatim

Run `skills/relationship-moves/SKILL.md` end to end against this workspace:

1. Entity-resolve + **MUST-run `live_contact_check` per candidate** — drop any the live check un-dormants.
2. Call `relationship_moves.compute_relationship_moves(WORKSPACE, top_n=3, thread_totals=…)` — it appends one `relationship_move_suggested` event per returned candidate.
   - **Surface-preference filter (Phase 6 Loop 2 — before drafting/rendering).** Drop any candidate the CEO has taught the system to stop surfacing: `from surface_preferences import load_surface_preferences, is_suppressed` → keep a candidate only if `not is_suppressed(prefs, "relationship-moves", item_class="relationship_move", entity_id=candidate.person_id)`. Missing store → no-op. Hides the suggestion only; the relationship record is untouched.
3. Draft the top-3 openers via the email-writer chain (voice per `shared/VOICE_CALIBRATION.md`).
4. Render ONE `all_batch_widget` (email-shaped items, canonical verbs).

# Phase 4 — Audit + STOP

Append the fire receipt via the canonical helper (`shared/scripts/receipts.py`, v4.5.2 R1 — **NEVER hand-roll the receipt JSON**): `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "relationship-moves", fired_via=<the Phase 2.9 receipt_fired_via: manual|scheduled|catchup>, surfaced=candidate_count)` — `receipt_fired_via` is what Phase 2.9's helper returned, never guessed. Then STOP — the widget + Links section is the whole turn.
