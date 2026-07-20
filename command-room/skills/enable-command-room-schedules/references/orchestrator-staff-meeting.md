# Orchestrator prompt — Staff Meeting

This file is the EXACT prompt the bootloader cats and executes for `taskId: staff-meeting`. Fires 9:00 AM Monday local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES` (`0 9 * * 1`) — the Sunday-evening maintenance jobs (deal-signals among them) have just refilled the Living Brain queue, and Friday already carries wrap (13:00) + triage (15:00). NEW in LB1 (R3). **NOT a first-install task** — propose-only later-add posture: it registers via `change-schedule` / Phase 6 `add` / the update-bridge proposal, never silently, never on a fresh workspace. Weekly by default; cadence is tunable via `change-schedule` like any chat (a second weekly slot is a user choice, never a default — cleanup's card-health line is the evidence for whether the queue earns it).

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. The Staff Meeting is a **full widget action surface** built from the shipped primitives (canonical per-item verbs per `shared/CHAT_ACTION_WIDGET.md` § Living Brain card; email verbs on outreach items; batch Apply-all footer + undo; per-item context notes; pagination as design): post via `widget_transport.render_and_persist` — the full validator chain (canonical verbs, leak scan, `validate_rendered_widget`) runs inside the one call — then pass `transport["html"]` (the persisted page's validated bytes, verbatim) to `mcp__visualize__show_widget` as `widget_code`, never hand-composed HTML (`shared/CHAT_ACTION_WIDGET.md` § Transport, F-15). Each page relays as widget_code — **size can never force improvisation**; pagination below is design, not a fallback.

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface the link block per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section". The narration lines + widget are the ENTIRE chat turn.

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for the Staff Meeting surface owned by `skills/system-health/SKILL.md` (§ "The Staff Meeting surface") — the same surface its on-demand `staff meeting` / `run our staff meeting` triggers fire. The queue projector is `shared/scripts/brain_proposals.py`, the narration reader is `shared/scripts/change_feed.py`, the moves section is `shared/scripts/relationship_moves.py` (R4 — REUSED, never forked), and resolutions dispatch through apply-choices (`src: "cr-brain"`). This orchestrator's job: (a) resolve paths, (b) execute that surface verbatim, (c) post ONE widget via the driver call that also writes the receipt (FB-7), (d) verify the receipt, (e) STOP.

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

**The narration + widget IS the chat turn. After it posts (plus the post-widget Links section), YOU STOP.** No exceptions. Applies to first fires AND re-runs.

**Forbidden — zero tolerance:**

1. **No writing the rendered widget to disk by hand** — the transport's own persist into `_hq/.system/widgets/` (performed by `render_and_persist` itself, per `shared/STOP_CONTRACT.md` rule 1) is the only sanctioned widget write.
2. **No narrating what's in the widget rows.** The user can see the queue. The two feed lines ("what I did on my own" / "what's waiting on you") are the ONLY narration.
3. **No post-widget summary block.** The turn ends after the widget + Links section.
4. **No resolving anything yourself.** Every resolution is a user click dispatched through apply-choices → the item's own writer. This surface proposes and narrates; it never adjudicates.
5. **No auto-send on outreach items.** `send` is always a user click dispatched through apply-choices → email-writer.
6. **No re-deciding inclusion.** The projector (`load_open_proposals`) and the moves machinery own who qualifies; render what they return, verbatim `render_line`s included (Bug #92b).
7. **No invented bulk verbs (FS-10, D10).** The ONLY verbs on a queue row are the registered per-row verbs the proposal carries (`confirm proposal` / `dismiss proposal` / `snooze proposal 7d` for brain rows; each legacy family's own shipped verbs). A widget-level "Confirm-close all," "Review the list," "Dismiss all," or any other bulk affordance you compose is FORBIDDEN — the standard Apply-all footer is the ONLY batch control, and it fires the rows the user selected. If you feel the queue needs a bulk action, it doesn't; that impulse is the FS-10 improvisation.
8. **No opaque clustering.** The queue renders as the `build_card_view` sections (MONEY / IDENTITY / HYGIENE with honest counts), each row `{name — badge · evidence-with-date · consequence}`. Never collapse the queue into hand-labeled buckets or a summary of "N deal signals, M cleanup items."

**Self-check before posting anything after the widget:** "is this required by spec?" If no → don't post it.

---

You are firing the Command Room "Staff Meeting" chat — the Living Brain's weekly review. Today is Monday in workspace LOCAL time. You're showing the CEO everything the brain did on its own since the last staff meeting, everything waiting on their eyes (the COMPLETE queue — this surface is deliberately exempt from the daily card's cross-surface dedup, R2), and this week's relationship moves, all resolvable in one sitting.

# Phase 1 — Always run (no idempotency gate)

This orchestrator ALWAYS runs when fired — by cron or the manual `staff meeting` / `run our staff meeting` trigger (which routes through system-health to this same surface). A `pack_run` receipt writes on every fire — INSIDE the Phase 5 driver call (`--fired-via`, FB-7); only the degrade branch writes it in Phase 6. Re-fires are safe: the queue projector is tombstone-aware, the moves machinery carries its own 7-day dedupe, and the driver never double-receipts a non-manual re-run.

# Phase 2 — Setup

The bootloader already resolved `PLUGIN_ROOT`, `WORKSPACE`, and this orchestrator file path. Continue with:

- Compute today's date in local time via `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` (REQUIRED `workspace_path`; on `TZResolutionError`, proceed with UTC and note it).
- Read `<WORKSPACE>/_hq/data/entities.json` — primary user, people, emails, relationship tiers.
- Discover connectors for the moves section's live-contact check (Mail native MCP — never Zapier for reads).

# Phase 2.9 — Run mode + lateness check (v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt; `manual` when a human caused the fire. **When uncertain, it is `manual`** (F-47 P1a).

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'staff-meeting', fired_via='<scheduled|manual>')))
"
```

Branch on `tier` exactly as every scheduled chat does:

- **`manual` / `none` / `exempt` / `unknown`** — run every phase normally, no timing narrative anywhere. A `suppressed` reason means the ledger found the slot served — believe it.
- **`note` (3–24h late)** — run ALL phases normally; the chat output OPENS with the returned `banner` line verbatim.
- **`degrade` (>24h late)** — do NOT render the surface. Execute every substrate write the fire owes (the expiry-safe projector reads write nothing; the receipt still writes — Phase 6's degrade branch, since the driver never runs), post ONLY the returned `degrade_notice` line, and STOP. The queue keeps; the next fire renders it.

Carry the returned `receipt_fired_via` into the Phase 6 receipt — never guess it independently.

# Phase 3 — Load the two halves (read-only)

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from brain_proposals import load_open_proposals, rank_proposals
from change_feed import changes_since
from receipts import iter_receipts
ws = '<workspace_root>'
# Since-marker: the last staff-meeting receipt; fallback 7 days ago.
prior = [r for r in iter_receipts(ws, task_ids=['staff-meeting'])]
since = prior[-1]['raw'].get('ts') if prior else '<ISO of now - 7d>'
feed = changes_since(ws, since)
queue = rank_proposals(load_open_proposals(ws, 'staff-meeting'))
print(json.dumps({'feed': feed, 'n_open': len(queue), 'queue': queue}, default=str))
"
```

- **"What I did on my own"** — the feed's lines, rendered verbatim (each is already plain English with its undo affordance where one applies). Cap 3 lines; drop-empty.
- **Unnamed-speaker count line (PID1 §0-4 — ONE prose line, this surface only).** Compute `n = identity_reconcile.count_open_annotations(ws)` (a separate read-only call — do not fold it into the queue load above). When `n > 0`, render ONE line after the feed lines: *"N unnamed speakers pending identification — resolving against calendars."* When `n == 0`, render nothing (drop-empty). This is the annotations' ONLY render anywhere — never a queue row, never a verb, never mentioned in the brief.
- **"What's waiting on you"** — the COMPLETE ranked queue (`surface="staff-meeting"` sees the full set — no daily-dedup filter, no cap). Zero items → the section says so honestly and the widget renders only the moves section (or the `all_clear_summary` data view when that's empty too).

  **⛔ THIS CARD IS NOW THE ONLY DOOR (FB-19 / FB-20 — M's ruling 2026-07-16).** The morning brief went read-only: it names deal signals in prose and points here, and nothing else in the system asks the user to confirm anything. Every item's ONLY path to adjudication runs through this card, which raises the bar for what may occupy a row:

  - **Every row states its ask and carries verbs, or it does not render.** A row that names no subject, asks no question, or offers no way to answer is not a row — it is a shrug, and it teaches the user to stop reading. This is enforced upstream in the projector (`brain_proposals._adapt_commitment_reviews` drops a review it cannot phrase an ask for), so an un-askable row never reaches the builder. Do NOT re-add one here, and do NOT invent a verb for a row that arrived without any (STOP rule 7).
  - **Held items are absent, not greyed.** `load_open_proposals` already filters them (FB-19's `mute_ledger.hold_item` writes a 14d `chat_dismissal` with `reason: "held"`). A parked item re-appearing is the live 2026-07-16 defect — it reads as the system ignoring what the user said. Never re-surface one "for completeness."
  - **Consequence floor — drop-empty, all the way up.** Sections tile only when they have rows; the builder drops empty shapes and refuses 0-value tiles. If NOTHING clears the bar — no money, no new identities, no actionable hygiene — then **render no queue card at all**: the moves section alone, or the `all_clear_summary` view when that's empty too. Never render an empty frame, a zero count, or a card whose only content is "nothing to review". An honest silence beats a card that wasted the trip.

# Phase 4 — "This week's moves" (R4 — the relationship-moves machinery, reused never forked)

Render the top-3 moves INSIDE this surface as a section, exactly as `skills/relationship-moves/SKILL.md` specifies:

1. **MUST-run `live_contact_check` per candidate** (`shared/scripts/live_contact_check.py`) — inherit the dormant-customer-scan MUST-language verbatim: NO dormancy-driven outreach from substrate-only data. Drop any candidate the live check un-dormants — EXCEPT candidates whose rank is carried by overdue commitments (overdue component >= dormancy component): keep the card and cite the recent touch in the why-now line.
2. Call `relationship_moves.compute_relationship_moves(WORKSPACE, top_n=3, thread_totals={})` — the default `emit=True` appends the `relationship_move_suggested` events, which is exactly what keeps this section and any still-registered standalone Relationship Moves chat from double-suggesting the same person inside 7 days (the machinery's own exclusion window — reuse gives the dedupe for free; existing registrations stay untouched, R4).
3. Apply the surface-preference filter (`surface_preferences.is_suppressed`, item_class `relationship_move`) before drafting.
4. Draft each opener via the **email-writer chain** exactly as the relationship-moves skill specifies (voice corpus → Voice Block fallback) — the opener chain already delegates correctly; do not restate or fork its rules here.
5. Section rows are email-shaped items with the standard verbs (`send` / `draft` / `snooze 3d`) — send lifecycle through apply-choices → email-writer, never reinvented.

Fewer than 3 → render fewer; zero (including everything deduped by a Sunday Relationship Moves fire) → drop the section. Never pad.

# Phase 5 — Render + post ONE widget (ONE driver call — T2.2)

**The entire build → fit → persist pipeline is ONE CLI invocation** — the
driver runs the Phase-3 projector (`load_open_proposals` + `rank_proposals`)
and the canonical builder (`build_card_view`) internally, then
`widget_transport.render_and_persist` (all validators + byte-fit + audit
persist). Do NOT hand-assemble sections or rows (FS-10 mechanization), and do
NOT re-run the Phase-3 loads separately for the render — the driver reads
them itself:

```bash
# moves.json = the Phase-4 rows (email-shaped item dicts), written to a temp
# file; omit --moves-json when Phase 4 produced zero rows.
python3 shared/scripts/surface_drivers.py staff-meeting \
    --workspace "<WORKSPACE>" --page 1 --moves-json <temp moves.json> \
    --fired-via "<the Phase 2.9 receipt_fired_via>"
```

**`--fired-via` is MANDATORY on the page-1 call — it is the receipt (FB-7).**
The driver appends the canonical `pack_run` receipt itself (via
`receipts.log_receipt`, the one chokepoint the manual `staff meeting` fire
also goes through) inside this same invocation, so the render and the
receipt can never be separated again — the 2026-07 live miss was exactly the
receipt living in a prose step after the widget post, where the STOP contract
ends the turn. A non-manual re-run within 15 minutes never double-receipts
(the RV-3 guard).

Stdout carries `CR-PAGINATION: {...}` (position metadata for the `show more`
narration) followed by the persisted page's validated bytes between
`CR-WIDGET-HTML-BEGIN` / `CR-WIDGET-HTML-END` markers, then
`CR-RECEIPT: {...}` confirming the receipt (`"status": "written"`, or
`"deduped_refire"` on an RV-3 re-run). **Relay the bytes between the markers
to `mcp__visualize__show_widget` as `widget_code`, byte-exact.** A `show
more` reply re-fires the SAME one-command driver with `--page N+1` — pages
2+ never write a receipt.

**Idempotent single call (RV-3):** run the driver exactly ONCE per page per fire
— if you already hold its output for the requested page, relay it; a
re-run persists a duplicate audit page (the commitments double-render
defect).

The driver's `build_card_view` produces the sections VERBATIM: MONEY / IDENTITY / HYGIENE, each titled with its **honest count**, each row `{name — badge · evidence-with-date · consequence}` carrying ONLY the proposal's registered verbs, rendered as the row's verb dropdown (T2.2; brain rows: `confirm proposal` / `dismiss proposal` / `snooze proposal 7d`; legacy rows: their own shipped verbs — enriched person rows carry `add person` / `proposal not relevant` / `snooze proposal 7d`). Every row embeds its proposal id + target ids VERBATIM (F2) with a sequential visible number (`display_n` — wire ids never render as row text, RV-5). The header count equals queue rows + moves rows (the RV-4 off-by-one fix — never restate a count yourself; the builder's header is the count). No row holds Apply on a missing input (F-17) — inputs are optional, empty applies as proposed. Do not add, relabel, or reorder verbs; do not compose a bulk verb (STOP rule 7).

- **Pagination as design:** page 1 first; the position line teaches `show more`; section titles keep the full honest counts on every page. Never chunk the post itself, never render the full unbounded set in one page (§ Transport).
- Footer: batch Apply-all + the standard undo affordance. The consolidated ack (apply-choices) ends with **"Say `undo` to reverse this."** — batch undo reverses commitment closes, mutes, and brain-batch auto-applies additively (`brain_undo.undo_batch`); never edit or delete prior events.

The two-line narration ("Since last Monday I …" from Phase 3, one line per half, drop-empty) goes ABOVE the widget; nothing goes below except the Links section.

# Phase 6 — Receipt verification + STOP

**Normal fires: the receipt already wrote INSIDE the Phase 5 driver call** (`--fired-via` — the `CR-RECEIPT: {...}` line after the END marker is the confirmation). Do NOT append a second one, and **NEVER hand-roll receipt JSON**.

The ONE branch that still writes its receipt here is **degrade** (Phase 2.9 — the surface never rendered, so the driver never ran): append it via the canonical helper: `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "staff-meeting", fired_via=<the Phase 2.9 receipt_fired_via>, surfaced=0)`.

Then STOP — narration + widget + Links section is the whole turn.
