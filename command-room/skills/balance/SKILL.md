---
name: balance
description: "The weekly personal white-space surface. Fires on: 'balance check', 'how's my white space', 'my white space', 'am I making time for family', 'plan a date night', plus 'tune balance'. Reads the personal lane ONLY — personal ties, personal reminders, open evenings on the declared personal/family calendars cross-checked against business busy — and surfaces the most-starved personal relationship with a pre-drafted reconnect pinned to a real open evening. Renders owner-only; nothing it emits reaches an org, board, or client surface — the firewall is the feature. Propose-and-confirm only: never books, sends, or spends without a click. Optional Sunday-morning task (later-add); no personal calendar connected → honest refusal, never all-clear. Does NOT fire on 'who should I reach out to' / 'weekly outreach' (relationship-moves — WORK ties), 'who went dark' (dormant-customer-scan), or 'show my reminders' (show-my-reminders). Fences: Routing section in the body."
---

# Balance — the weekly personal white-space surface (SPEC BAL1)

One `m_facing` widget, Sunday morning: the coldest personal relationship with at least one genuinely open evening, a pre-drafted reconnect, and the open slots to pin it to. Flagship case: "You and [spouse] — last date night 7 weeks ago. Thu and Sat are open after 6 PM. Want me to book [restaurant]?" The surface exists to protect white space — it notices when the personal side has gone dark and proposes the reconnect.

**The firewall IS the feature.** Everything this skill reads and emits is personal-lane: `tie: "personal"` people, `personal: true` reminders, `balance_nudge_suggested` events. The org-scoped reader (`events_io.load_events_org_scoped`) drops all of it from every org/board/client/external output by design, `personal_leak.is_personal` classifies it, and Pulse's cadence math excludes it. Any reader of this surface that is not `m_facing` gets nothing.

## Skill Boundary

- **Is:** the personal-domain twin of relationship-moves — decay-first ranking over `tie: "personal"` people, plus the open-evening detector that makes the nudge actionable.
- **Is NOT** relationship-moves (WORK ties — the two partition the entity set; a person is in exactly one lane), dormant-customer-scan (customer detection), or show-my-reminders (the reminder dump — Balance is proactive and relationship-aware).

## Step 0 — Config gate (not-configured ≠ healthy)

Resolve the workspace per `shared/CONTRACT.md` Rule 22. Read `entities.json` → `workspace.personal_calendars`. **If none are declared, STOP with the honest refusal** — say: *"Connect a personal calendar to turn on Balance — I can only protect evenings I can see."* Emit nothing. Never render an all-clear from an unconfigured state (`compute_balance` enforces this too — its `not_configured` return).

Config surface (all under `workspace` in `entities.json`, set via workspace-manager / first-run):
- `personal_calendars`: list of calendar ids (personal Google primary + shared family calendar; an ICS feed id for Skylight-style clients — there is NO native Skylight connector, do not promise one).
- `evening_start` (default `"18:00"`), `evening_end` (default `"22:00"`), `min_block_hours` (default 2).
- `balance_default_cadence_days` (default 14) — the cadence used for a personal tie with no `cadence_days` of its own.

## Step 1 — Fetch busy intervals (both calendars, always both)

1. Compile the personal-calendar queries in code — `balance.personal_calendar_query_specs(entities, now=<local now>, horizon_days=14, provider=<discovered provider>)` — so the fetch targets the DECLARED calendars through `connector_adapters/calendar.py::calendar_addressing_field`. Execute each spec through the discovered calendar tool (native MCP, never Zapier for calendar).
2. Fetch business busy for the same window from the business calendar/availability connector.
3. Localize every returned time via `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` before passing intervals on — never trust upstream times.
4. **If either fetch fails, pass `None` for that side** — `compute_balance` returns its `no_calendar_data` refusal. Never propose an evening against a calendar you couldn't read (a work-travel block you didn't see is exactly the failure this kills). An empty calendar is `[]`, not `None`.

## Step 2 — Compute (code, never prose math)

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT"
```

```python
import sys; sys.path.insert(0, "shared/scripts")
from balance import compute_balance
result = compute_balance("<abs WORKSPACE root>", horizon_days=14,
                         personal_busy=personal_busy, business_busy=business_busy)
```

`compute_balance` loads `tie: "personal"` people + their `cadence_days` (read by balance ONLY — never routed through dormancy), personal reminders via the reminders `m_facing` surface gate, computes open evenings via `availability.open_evenings` (deterministic interval subtraction — personal + family + business busy all subtracted), ranks decay-first (`gap_days / cadence_days`; a starved tie with zero open slots is suppressed, not surfaced as guilt), dedupes (7-day per-tie window, active snoozes/dismissals honored), and emits **at most one** `balance_nudge_suggested` (personal-lane, `data.personal: true`). You do not compute scores, gaps, or free slots in prose.

Branch on `result["status"]`:
- `not_configured` / `no_calendar_data` → the Step 0/Step 1.4 refusal line. STOP.
- `all_clear` → render the `all_clear_summary` data view ("White space looks healthy this week."), never hand-built HTML. STOP after the widget.
- `nudge` → Steps 3–4.

## Step 3 — Draft the reconnect (email-writer chain, warm register)

When the nudge's `proposed_action.kind` is `"reservation"` and a venue is in play, draft the venue outreach via the **email-writer chain** (voice = the warm register per `shared/VOICE_CALIBRATION.md`) — this skill never writes reservation/outreach text directly. No venue → the reconnect card carries the why-now + slots without an email leg. Drafts are NEVER auto-sent.

## Step 4 — Render ONE widget (m_facing) + STOP

Render through `widget_transport.render_and_persist` with the data view's `surface: "m_facing"`, then pass `transport["html"]` to `mcp__visualize__show_widget` byte-exact. Two shapes:

1. **Reconnect card** — item = "[You] + [tie name]", `context_tag` = "last [date] · [N] wks", `body_lines` = the why-now line + the 2–3 open slots (plain language: "Thu Jul 24 and Sat Jul 26 are open after 6 PM"), actions `["book", "propose other night", "snooze 7d", "skip"]` — all four are in `CANONICAL_ACTIONS` (the `book` / `propose other night` rows are BAL1's, `shared/scripts/verb_taxonomy.py`).
2. **Venue-outreach variant** — email-shaped item with `metadata=[["To", venue_email], ["Subject", subj]]`, dispatching through email-writer.

Post-widget Links/Sources section, then STOP — the widget is the entire turn. Zero nudges → the `all_clear_summary` view. **Output guard:** no internal tokens (`tie`, event names, scores) in anything rendered — "last touch 49 days ago", never "score 3.5".

## Confirm path (D4 — propose-and-confirm, the hard line)

Nothing below happens at render time. Only on an explicit `book` click from Apply:

1. **Tentative hold:** route the personal-calendar hold through `calendar-writer`'s Phase 5/6 propose-and-confirm write path (discover the create tool, write only on the explicit confirm dispatch) — never a direct calendar write. The hold targets the PERSONAL calendar id from `workspace.personal_calendars`.
2. **Venue outreach:** queue the Step-3 draft per the workspace draft posture (Drafts queue on the user's action — never on render, never auto-sent).
3. Write the linkage: update nothing in place — append the follow-on linkage as a `balance_nudge_actioned` event (`data.personal: true` always — the type is personal-classified via `personal_leak._PERSONAL_EVENT_TYPES`, so org-scoped readers drop it; never a generic or org-lane type) with `proposed_action.draft_event_seq` populated (append-only discipline).

On `propose other night` with a typed date: validate the date via `availability.has_conflict` against the SAME busy set from Step 1 before drafting anything; a conflicted date gets an honest "that evening has [conflict] — closest open is [slot]". Empty input → re-render the remaining `open_slots` from this fire's computation.

**No code path books a reservation, pays, or sends autonomously — a reservation is a commitment + potential financial action; both are user-click-gated, and OpenTable/Resy-style auto-booking is rejected for v1, not deferred.**

## What this skill does NOT do

- Does not read or rank WORK ties (relationship-moves' lane — the `tie` field partitions the entity set).
- Does not emit dormancy signals, pattern-breaks, or anything an org surface consumes.
- Does not auto-book, auto-send, or spend — every outward action is an explicit click.
- Does not render an all-clear when unconfigured — it refuses honestly.
- Does not nag — 7-day per-tie dedupe, snoozes/dismissals honored, never pads below one real nudge.

## Tune (first-run personalization)

`tune balance` / `show balance settings` / `reset balance to defaults` — per `shared/SKILL_CUSTOMIZATION.md`: evening window (`evening_start`/`evening_end`), `min_block_hours`, `balance_default_cadence_days`, and per-tie `cadence_days` ("set date-night cadence to 2 weeks" → `people_writer.update_person(..., cadence_days=14)`).

## Routing (full trigger corpus)

> The weekly personal white-space surface: the single most-starved personal relationship, a pre-drafted reconnect, and the genuinely open evenings to pin it to — computed in code from the declared personal + family calendars cross-checked against business busy. Triggers: 'balance check', 'how's my white space', 'my white space', 'am I making time for family', 'am I making time for personal stuff', 'plan a date night', 'tune balance', 'show balance settings', 'reset balance to defaults'. Bare white space and bare date night are deliberately UNOWNED (the same rule that keeps the bare word deal unowned) — a design/layout white-space remark or a date-night mention inside an unrelated ask must not fire this skill; only the my-white-space / plan-a-date-night shapes route here. DOES NOT fire on 'who should I reach out to' / 'who should I reach out to this week' / 'weekly outreach' / 'relationship moves' (relationship-moves — WORK ties; the two surfaces partition the entity set on the `tie` field). DOES NOT fire on 'who went dark' / 'dormant customer scan' / 'quiet customers' (dormant-customer-scan — customer detection). DOES NOT fire on 'show my reminders' / 'my reminders' (show-my-reminders — the pin list; Balance is proactive and relationship-aware, not a reminder dump). DOES NOT fire on 'schedule a [length] with [name]' / 'book time with [name]' (calendar-writer — work scheduling; Balance's `book` is a widget verb, not a chat trigger). The line: relationship-moves ranks WORK outreach; Balance protects PERSONAL white space.
