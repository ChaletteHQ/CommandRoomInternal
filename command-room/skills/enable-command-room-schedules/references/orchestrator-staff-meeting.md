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
8. **No opaque clustering — the HAND-clustering ban, unchanged.** The queue renders as the `build_card_view` sections (MONEY / IDENTITY / HYGIENE with honest counts), each row `{name — badge · evidence-with-date · consequence}`. Never collapse the queue into hand-labeled buckets of your own, and never replace it with a summary of "N deal signals, M cleanup items."

   **This ban is about YOUR improvisation, not about grouping as such.** Since STAFFCUT (2026-08-02) the driver itself emits EVIDENCE-CLASS DIGEST rows — one row standing for every item that rests on the same evidence — and those are not the thing this rule forbids. The difference is total: a digest is built by `proposal_digests.group_into_digests` inside the driver, it states its own honest member count, it carries every member's id verbatim in `data.digest_members`, and each member is still adjudicated by its own handler through the same fence. A hand-labeled bucket is a sentence you wrote that nobody can click. Render the driver's digests exactly as it returns them (`render_line` verbatim, Bug #92b) and compose none of your own.

**Self-check before posting anything after the widget:** "is this required by spec?" If no → don't post it.

---

You are firing the Command Room "Staff Meeting" chat — the Living Brain's weekly review. Today is Monday in workspace LOCAL time. You're showing the CEO everything the brain did on its own since the last staff meeting, everything waiting on their eyes (the COMPLETE queue — this surface is deliberately exempt from the daily card's cross-surface dedup, R2), and this week's relationship moves, all resolvable in one sitting.

# Phase 1 — Always run (no idempotency gate)

This orchestrator ALWAYS runs when fired — by cron or the manual `staff meeting` / `run our staff meeting` trigger (which routes through system-health to this same surface). A `pack_run` receipt writes on every fire — INSIDE the Phase 5 driver call (`--fired-via`, FB-7); only the degrade branch writes it in Phase 6. Re-fires are safe: the queue projector is tombstone-aware, the moves machinery carries its own 7-day dedupe, and the driver never double-receipts a non-manual re-run.

# Phase 2 — Setup

The bootloader already resolved `PLUGIN_ROOT`, `WORKSPACE`, and this orchestrator file path. Continue with:

- Today's date is `clock["today"]` from the Phase 2.9 return (CLOCK1) — the corroborated instant, already expressed in the workspace timezone by code. Never compute it from this computer's clock: an unsynced sandbox clock reading two days behind is what surfaced a meeting that had already happened as upcoming. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` exactly as before (REQUIRED `workspace_path`; on `TZResolutionError`, proceed with UTC and note it).
- Read `<WORKSPACE>/_hq/data/entities.json` — primary user, people, emails, relationship tiers.
- Discover connectors for the moves section's live-contact check (Mail native MCP — never Zapier for reads).

# Phase 2.9 — Run mode + lateness check (v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt; `manual` when a human caused the fire. **When uncertain, it is `manual`** (F-47 P1a).

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'staff-meeting', fired_via='<scheduled|manual>', env_date='<session date>')))
"
```

**Every python subprocess in this fire carries `CR_WORKSPACE` (CLOCK1).** Prefix them: `CR_WORKSPACE=<WORKSPACE> python3 -c "..."`. Each `python3 -c` is its own process started from the plugin root, so a helper left to guess which workspace it is in finds nothing, cannot cross-check the clock, and stamps whatever this computer says. The phases that run BEFORE the lateness check write to the ledger too, which is exactly where an unchecked clock does its permanent damage.

**Pass the session date too (CLOCK1).** `env_date` is this session's own date — the `Today's date is YYYY-MM-DD` line in your context. It is the second source the run cross-checks this computer's clock against, and the only one that can catch a clock running fast. Substitute the date and nothing else; if you genuinely do not have one, pass an empty string. A value that is not a date is treated as absent: it never moves the clock and never blocks the fire.

**The clock verdict comes back as `clock`, and two things follow from it. Neither is optional:**

- **When `clock["notice"]` is set, it is the FIRST line of this fire's output** — above the lateness banner, verbatim, never paraphrased and never dropped. It states that the dates in this surface came from the workspace record rather than this computer's clock. A silent substitution is its own bug: the reader has no other way to know which clock produced what they are looking at.
- **Today's date is `clock["today"]`** — take it from the return rather than computing one here.


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
- **"What's waiting on you"** — the ranked queue. `surface="staff-meeting"` sees the full PROJECTION (no daily-dedup filter), and the driver then bounds what ONE FIRE RENDERS to about two screens. Zero items → the section says so honestly and the widget renders only the moves section (or the `all_clear_summary` data view when that's empty too).

  **The queue is BOUNDED per fire since STAFFCUT (2026-08-02) — this replaces the old "no cap" contract.** The 2026-08-02 load audit measured one fire at 105 rows over 7 screens, with three kinds accounting for 93 of the 101 queue rows, so "render everything, every week" had stopped being honesty and become the reason the page went unread. Two driver-side passes now sit between the projector and the builder, and **you do not perform either of them** — `surface_drivers.build_staff_meeting_view` does, inside the one Phase-5 call:

  - **Evidence-class digests** (`proposal_digests.group_into_digests`): sent-match review rows group by their EVIDENCE (16 distinct lines carried 54 rows on the audit day, 34 of them on one line), name-only identity mentions group into one "N new names from your calls" row, and unadjudicated fact rows group into one. A digest changes PRESENTATION only: every member keeps its own id (`data.digest_members`), its own verbs and its own resolution path, a grouped confirm rides the same shared bulk-accept fence, and nothing auto-closes.
  - **A page bound** (`proposal_digests.bound_page`, default `STAFF_PAGE_ROW_CAP` = 21 rows for the whole page, appended sections included): the meeting fold's shipped volume guard applied to the queue lane. Budget is split across the shapes present so no lane is starved, the ranked FRONT of each lane is what shows, and the honest full totals plus a pointer ride the section titles (`IDENTITY (8) — 25 items grouped into 8 rows · showing the front 8 of 24 — the rest stay queued and lead the next one`). It bounds the PAGE-SET, never the projector: nothing is resolved, nothing is hidden, and answering the front is what advances the rotation.

  **The header count still equals the rows the widget shows (RV-4) — never restate a count yourself.** The full arithmetic lives in the section titles for exactly that reason. Read them; do not recompute them.

  **One honesty convention, and the tiles are on it (LIFECYCLE1 §7b, M's walk 2026-08-03).** The header tiles used to count the PAGE while the section titles carried the FULL totals — tiles reading 1 / 7 / 6 above titles saying 41 and 18, same widget, same rows. The tiles now show the honest per-shape total of everything open (`build_card_view(shape_totals=...)`, supplied by the driver from the full queue); the page bound governs what RENDERS and nothing else. Do not "reconcile" a tile against the rows you can count on screen — they are answering different questions on purpose, and the section notes say which is which.

  **Two kinds no longer appear here at all**, and their absence is the ruling, not a bug: **dormancy** rows are ON-DEMAND (M ruling 2026-08-02 — whether a quiet project is dormant is a judgment the CEO makes when he asks; the asking surface is `stalled projects`, and `load_open_proposals(ws, "on-demand")` is the read), and **schedule_add** rows are RETIRED at the projector (`brain_proposals.RETIRED_KINDS`) with the writer stopped too (LIFECYCLE1 §7a — STAFFCUT retired the legacy adapter, but the migrated bp-rail writer kept minting rows and one of them rendered here the next morning; 0 of 4 ever produced a registration, which only ever happens through the change-schedule path). Do not add either back "for completeness."

  **⛔ THIS CARD IS NOW THE ONLY DOOR (FB-19 / FB-20 — M's ruling 2026-07-16).** The morning brief went read-only: it names deal signals in prose and points here, and nothing else in the system asks the user to confirm anything. Every item's ONLY path to adjudication runs through this card, which raises the bar for what may occupy a row:

  - **Every row states its ask and carries verbs, or it does not render.** A row that names no subject, asks no question, or offers no way to answer is not a row — it is a shrug, and it teaches the user to stop reading. This is enforced upstream in the projector (`brain_proposals._adapt_commitment_reviews` drops a review it cannot phrase an ask for), so an un-askable row never reaches the builder. Do NOT re-add one here, and do NOT invent a verb for a row that arrived without any (STOP rule 7).

    **This contract was being violated by the projector itself until STAFFCUT.** Three legacy adapters shipped `action_tuples: []` hardcoded — org/project, dormancy, schedule_add — so on the audit day six org rows and one dormancy row rendered with NO BUTTONS: permanently unanswerable, sitting on the surface that is the only door. `propose()` refuses empty tuples at source, so those fossil adapters were the only way to produce one. All three are now resolved in code: org/project rows carry `confirm [type]` / `not relevant` (the verbs apply-choices was already dispatching for those kinds), dormancy rows carry `active` / `archive` / `snooze 14d`, and schedule_add is retired. If you ever see a buttonless row again, that is a projector defect to report — never something to paper over with a verb of your own.
  - **Held items are absent, not greyed.** `load_open_proposals` already filters them (FB-19's `mute_ledger.hold_item` writes a 14d `chat_dismissal` with `reason: "held"`). A parked item re-appearing is the live 2026-07-16 defect — it reads as the system ignoring what the user said. Never re-surface one "for completeness."
  - **From your meetings (CAPTUREFLOW §C, 2026-08-01) — a SECTION, not an appointment.** The Phase-5 driver appends ONE extra section, `FROM YOUR MEETINGS (N items, K calls)`, built by `needs_review_queue.staff_meeting_group_section` — the SAME per-meeting grouping the on-demand `needs your call` queue renders, from the same builder, with the same `confirm` / `already done` / `drop` / `not mine` verbs answered through the same shared fence (`needs_review_queue.confirm_items` / `needs_review_queue.done_items` → `watch_gate.screen_bulk_accept`). The verb list is `needs_review_queue.QUEUE_ROW_ACTIONS`, read by both render sites — never re-typed per surface. **`already done` (DONE1, 2026-08-03)** is the answer for a promise the CEO already kept off-mail: it confirms the capture and closes it as `done` on the CEO's own attestation, so those items stop being counted as dismissals of that counterparty's captures the way a `drop` is. It is PER-ITEM ONLY — every id must be named on its own, so `all`, a range and a call phrase never reach it. One queue, two places to answer it; there is no second ledger and no new scheduled task. You do NOT build this section — the driver does. Its volume guard is built in: whole calls only (a split call asks half a question), oldest call first, at most 3 calls and 8 rows, honest full totals in the title, and a pointer to `needs your call` for the remainder. Oldest-first IS the rotation rule — the front of the queue is what shows, so no call can be suppressed forever. **What the rows are (2026-08-01):** the same three kinds the on-demand queue carries — an unsure extraction, a capture whose evidence was not found in its transcript (`data.fusion_unverified`), and a capture the admission floor gated (`data.floor_gated`, its `FLOOR_*` reason printed on the row). The third kind used to be dropped silently; M ruled it routes here instead, because the floor was measured wrong as often as right and a silent drop destroyed real promises. Render all three the same way — they are one question, *did we hear this right?* — and never filter a `floor_gated` row out of the section.
  - **Consequence floor — drop-empty, all the way up.** Sections tile only when they have rows; the builder drops empty shapes and refuses 0-value tiles. If NOTHING clears the bar — no money, no new identities, no actionable hygiene — then **render no queue card at all**: the moves section alone, or the `all_clear_summary` view when that's empty too. Never render an empty frame, a zero count, or a card whose only content is "nothing to review". An honest silence beats a card that wasted the trip.

# Phase 4 — "This week's moves" (R4 — the relationship-moves machinery, reused never forked)

Render the top-3 moves INSIDE this surface as a section. **Branch on the Phase 2.9 run mode FIRST (WG1-B D-B4 — big-test row 10a: scheduled fires produced zero renderable rows and the section silently vanished):**

**`scheduled` fires — the deterministic, connector-free path.** No email-writer chain, no connector reads, no improvised row assembly:

1. Call `relationship_moves.compute_relationship_moves(WORKSPACE, top_n=3, thread_totals={})` — the default `emit=True` keeps the 7-day dedupe exactly as on the manual path.
2. Apply the surface-preference filter (`surface_preferences.is_suppressed`, item_class `relationship_move`).
3. Convert the survivors via `relationship_moves.moves_rows_from_candidates(<candidates>, WORKSPACE)` — the canonical adapter emits complete data-view rows (`n: "move:<person_id>"`, resolved display name, substrate-derived why-now tag, verbs `nudge` / `snooze 3d` / `not relevant`). Never hand-shape a row; an unresolvable person_id is the adapter's to skip.
4. Write the adapter's rows to the temp file and pass `--moves-json` whenever it returned **≥1 row** — the omit-when-zero path now fires only on genuinely zero candidates. `nudge` is compose-on-CLICK (WG1-A D-A4): the chase draft AND the live-contact check both run at apply time in the click-handling chat — this fire touches no connector.

**`manual` fires — the interactive path, unchanged (row 27 verified it live):**

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
2+ never write a receipt, and they slice the page-set page 1 froze rather
than re-reading the substrate (PAGESNAP; see `shared/CHAT_ACTION_WIDGET.md`
§ "A page-set is ONE question asked ONCE"). This is the 2026-07-28 defect
where page 2 repeated page-1 rows 14/15 and the header moved 18 -> 22 mid
fire. If `CR-PAGINATION` carries `refreshed`, `suppressed`, or `clamped`,
SAY it in one line before the rows.

**Idempotent single call (RV-3):** run the driver exactly ONCE per page per fire
— if you already hold its output for the requested page, relay it; a
re-run persists a duplicate audit page (the commitments double-render
defect).

The driver's `build_card_view` produces the sections VERBATIM: MONEY / IDENTITY / HYGIENE, each titled with its **honest count**, each row `{name — badge · evidence-with-date · consequence}` carrying ONLY the proposal's registered verbs, rendered as the row's verb dropdown (T2.2; brain rows: `confirm proposal` / `dismiss proposal` / `snooze proposal 7d`; legacy rows: their own shipped verbs — enriched person rows carry `add person` / `proposal not relevant` / `snooze proposal 7d`). Every row embeds its proposal id + target ids VERBATIM (F2) with a sequential visible number (`display_n` — wire ids never render as row text, RV-5). The header count equals queue rows + moves rows (the RV-4 off-by-one fix — never restate a count yourself; the builder's header is the count). No row holds Apply on a missing input (F-17) — inputs are optional, empty applies as proposed. Do not add, relabel, or reorder verbs; do not compose a bulk verb (STOP rule 7).

- **Pagination as design:** page 1 first; the position line teaches `show more`; section titles keep the full honest counts on every page. Never chunk the post itself, never render the full unbounded set in one page (§ Transport). Since STAFFCUT the page-set is itself bounded (~2 pages), so `show more` walks the fire's own page-set; the rows the bound held back are not on any page of this fire — they stay queued and lead the next one, which is what the section-title pointer says.
- Footer: batch Apply-all + the standard undo affordance. The consolidated ack (apply-choices) ends with **"Say `undo` to reverse this."** — EXCEPT when the batch included a record merge (`merge person records`): then the ack instead ends **"Say `undo` to reverse this — except the record merge, that one is permanent."** (UXC1 — the blanket undo promise must never cover the one action it cannot reverse.) — batch undo reverses commitment closes, mutes, and brain-batch auto-applies additively (`brain_undo.undo_batch`); never edit or delete prior events.

The two-line narration ("Since last Monday I …" from Phase 3, one line per half, drop-empty) goes ABOVE the widget; nothing goes below except the Links section.

# Phase 6 — Receipt verification + STOP

**Normal fires: the receipt already wrote INSIDE the Phase 5 driver call** (`--fired-via` — the `CR-RECEIPT: {...}` line after the END marker is the confirmation). Do NOT append a second one, and **NEVER hand-roll receipt JSON**.

Since STAFFCUT that receipt also carries PER-KIND counts (`open_by_kind`, `surfaced_by_kind`) and the digest/bound arithmetic (`queue_rows_rendered` vs `queue_items_represented`, `digest_rows`, `page_bound`) on `extra_data`. The scalar `surfaced` keeps its exact meaning — the rows the widget showed. This is additive telemetry so load history is MEASURABLE: the 2026-08-02 audit had to reconstruct 23 fires from an upper-bound model because no receipt had ever recorded what was surfaced. Nothing here is for you to compute, narrate, or repeat in the chat.

The ONE branch that still writes its receipt here is **degrade** (Phase 2.9 — the surface never rendered, so the driver never ran): append it via the canonical helper: `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "staff-meeting", fired_via=<the Phase 2.9 receipt_fired_via>, surfaced=0)`.

Then STOP — narration + widget + Links section is the whole turn.
