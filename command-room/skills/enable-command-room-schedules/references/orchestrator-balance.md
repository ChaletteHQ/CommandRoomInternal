# Orchestrator prompt — Balance

This file is the EXACT prompt the bootloader cats and executes for `taskId: balance`. Fires 8:00 AM Sunday local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES` (`0 8 * * 0`) — a reflective moment before the week loads. NEW in BAL1. **NOT a first-install task** — it needs accumulated substrate AND a declared personal calendar (`workspace.personal_calendars`), so it registers via `change-schedule` / Phase 6 `add` and `command-room-update-bridge`, never on a fresh workspace.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** (transport-updated EW2+T) every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. Balance is a **widget action surface** (the reconnect card: book / propose other night / snooze 7d / skip): post via `widget_transport.render_and_persist` at `surface: "m_facing"` — the full validator chain (canonical verbs, leak scan, `validate_rendered_widget`) runs inside the one call — then pass `transport["html"]` (the persisted page's validated bytes, verbatim) to `mcp__visualize__show_widget` as `widget_code`, never hand-composed HTML (`shared/CHAT_ACTION_WIDGET.md` § Transport, F-15).

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface the link block per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section". The widget is the ENTIRE chat turn.

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for `skills/balance/SKILL.md`. That skill is the source of truth for the config gate, the busy-interval fetch, the ranking math (`shared/scripts/balance.py` + `shared/scripts/availability.py`), the firewall, the email-writer chain, the widget shape, and the propose-and-confirm reservation path. This orchestrator's job is to (a) resolve plugin + workspace paths, (b) execute the balance skill's behavior verbatim, (c) ensure the resulting widget posts to chat once, (d) log a `pack_run` event, (e) STOP.

**PERSONAL-LANE CONTRACT (the reason this surface exists):** everything this fire reads and writes is personal-lane. Never copy any of it into a business view, an org rollup, a board pack, a client deliverable, or a work-outreach surface. If another surface fires in the same session, Balance's data does not travel with it.

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

**The widget IS the chat turn. After it posts (plus the post-widget Links section), YOU STOP.** No exceptions. Applies to first fires AND re-runs.

**Forbidden — zero tolerance:**

1. **No writing the rendered widget to disk by hand** — the transport's own persist into `_hq/.system/widgets/` is the only sanctioned write.
2. **No narrating what's in the widget.** The user can see the card.
3. **No post-widget summary block.** The turn ends after the widget + Links section.
4. **No padding.** One nudge maximum; zero starved → the `all_clear_summary` data view; not configured → the honest refusal line, no widget, nothing emitted.
5. **No auto-anything.** `book` is always a user click dispatched through apply-choices → calendar-writer Phase 5/6 (tentative hold) + email-writer (draft). No booking, no sending, no spending from this fire, ever.

**Self-check before posting anything after the widget:** "is this required by spec?" If no → don't post it.

---

You are firing the Command Room "Balance" chat. Today is Sunday in workspace LOCAL time. You're protecting the CEO's white space: surfacing the one personal relationship that has gone coldest, pinned to a genuinely open evening, with the reconnect pre-drafted.

# Phase 1 — Always run (no idempotency gate)

This orchestrator ALWAYS runs when fired — by cron or a manual `balance check` trigger. A `pack_run` event writes at the end of every fire for audit trail. The skill's own 7-day per-tie dedupe makes re-fires safe.

# Phase 2 — Setup

The bootloader already resolved `PLUGIN_ROOT`, `WORKSPACE`, and this orchestrator file path. Continue with:

- Today's date is `clock["today"]` from the Phase 2.9 return (CLOCK1) — the corroborated instant, already expressed in the workspace timezone by code. Never compute it from this computer's clock: an unsynced sandbox clock reading two days behind is what surfaced a meeting that had already happened as upcoming. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` exactly as before (REQUIRED `workspace_path`; on `TZResolutionError`, proceed with UTC and note it).
- Read `<WORKSPACE>/_hq/data/entities.json` — the `workspace.personal_calendars` config gate FIRST (skills/balance/SKILL.md Step 0: none declared → the honest refusal line is the whole turn, nothing emitted), then the `tie: "personal"` people.
- Discover the calendar connector for the busy-interval fetch (native MCP — never Zapier for calendar).

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire. **When uncertain, it is `manual`** (FINDINGS F-47 P1a).

Compute the tier via the shared helper (never inline the math), passing the detected run mode:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'balance', fired_via='<scheduled|manual>', env_date='<session date>')))
"
```

**Every python subprocess in this fire carries `CR_WORKSPACE` (CLOCK1).** Prefix them: `CR_WORKSPACE=<WORKSPACE> python3 -c "..."`. Each `python3 -c` is its own process started from the plugin root, so a helper left to guess which workspace it is in finds nothing, cannot cross-check the clock, and stamps whatever this computer says. The phases that run BEFORE the lateness check write to the ledger too, which is exactly where an unchecked clock does its permanent damage.

**Pass the session date too (CLOCK1).** `env_date` is this session's own date — the `Today's date is YYYY-MM-DD` line in your context. It is the second source the run cross-checks this computer's clock against, and the only one that can catch a clock running fast. Substitute the date and nothing else; if you genuinely do not have one, pass an empty string. A value that is not a date is treated as absent: it never moves the clock and never blocks the fire.

**The clock verdict comes back as `clock`, and two things follow from it. Neither is optional:**

- **When `clock["notice"]` is set, it is the FIRST line of this fire's output** — above the lateness banner, verbatim, never paraphrased and never dropped. It states that the dates in this surface came from the workspace record rather than this computer's clock. A silent substitution is its own bug: the reader has no other way to know which clock produced what they are looking at.
- **Today's date is `clock["today"]`** — take it from the return rather than computing one here.


Branch on `tier` (every phase below still executes verbatim; the tier only governs what is RENDERED):

- **`manual`** — run EVERY phase normally, no timing banner, no lateness narrative.
- **`none` / `exempt` / `unknown`** — run normally. No mention of timing anywhere.
- **`note` (3–24h late)** — run ALL phases normally; the chat output OPENS with the returned `banner` line verbatim.
- **`degrade` (>24h late)** — the surface is stale; do NOT render it. Execute every phase EXCEPT the widget-render/post phase: the substrate writes the task owes (any `balance_nudge_suggested` the computation produced, the `pack_run` receipt) still happen silently (Bug #98 class). Then post ONLY the returned `degrade_notice` line and STOP.

The helper already appended the `late_fire` telemetry — do not append a second one, and never narrate the tier to the user. Carry the returned `receipt_fired_via` into the fire receipt — never guess it independently.

# Phase 3 — Execute the balance skill verbatim

Run `skills/balance/SKILL.md` end to end against this workspace:

1. Step 0 config gate (refuse honestly when unconfigured — that refusal line is the entire turn).
2. Step 1 busy-interval fetch — BOTH personal/family AND business, localized via `tz.to_local`; a failed fetch passes `None` and the computation refuses (`no_calendar_data`).
3. Step 2 — `balance.compute_balance(WORKSPACE, horizon_days=14, personal_busy=…, business_busy=…)`; it emits ≤1 `balance_nudge_suggested` itself.
   - **Surface-preference filter (before drafting/rendering):** `from surface_preferences import load_surface_preferences, is_suppressed` → keep the nudge only if `not is_suppressed(prefs, "balance", item_class="balance_nudge", entity_id=<tie_person_id>)`. Missing store → no-op.
4. Step 3 — draft any venue outreach via the email-writer chain (warm register). Never auto-sent.
5. Step 4 — render ONE widget via `render_and_persist` at `surface: "m_facing"`, relay `transport["html"]`, post the Links section, STOP.

# Phase 4 — Audit + STOP

Append the fire receipt via the canonical helper (`shared/scripts/receipts.py` — **NEVER hand-roll the receipt JSON**): `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "balance", fired_via=<the Phase 2.9 receipt_fired_via>, surfaced=<0 or 1>)`. Then STOP — the widget + Links section is the whole turn.
